"""Bounded, no-booking replay of real source mail against two text nodes.

Run inside the API environment with explicit provider IDs. No worker, event,
candidate, queue, audit or booking write functions are called. Does not unload
models, change routing, seeds, context size or keep-alive configuration.
Output contains IDs/decisions/measurements, never mail bodies or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from types import SimpleNamespace


def run(ids: list[str], repeats: int = 2) -> None:
    from core import recruitment_mail_store as store
    from core.ollama_nodes import configured_nodes
    from services import recruitment_mail_agent as agent

    if not ids or len(ids) > 12 or not 1 <= repeats <= 3:
        raise ValueError('Use 1–12 explicit source IDs and 1–3 repeats')
    with store.get_connection() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute('SELECT * FROM mailbox_messages WHERE provider_message_id=ANY(%s)', (ids,))
        rows = store._rows(cur)
    if len(rows) != len(set(ids)):
        raise ValueError('Every source ID must resolve exactly once')
    corpus = []
    for row in sorted(rows, key=lambda r: ids.index(r['provider_message_id'])):
        message = {**row, 'body': row.get('body_text') or ''}
        attachments = [{**a, 'text': a.get('extracted_text') or ''}
                       for a in store.attachments_for_message(row['id'], include_text=True)]
        corpus.append((message, attachments))

    def emit(value):
        print(json.dumps(value, default=str), flush=True)

    for node in configured_nodes():
        if node['id'] not in {'rtx4060', 'jagadeesh'}:
            continue
        def get(path):
            with urllib.request.urlopen(node['base_url'] + path, timeout=5) as response:
                return json.load(response)
        emit({'node': node['id'], 'phase': 'before', 'version': get('/api/version'), 'ps': get('/api/ps')})

        def read_model(*, messages, schema, model, **kwargs):
            payload = {'model': model, 'messages': messages, 'format': schema,
                       'stream': False, 'options': {'temperature': 0},
                       'keep_alive': (os.getenv('OLLAMA_KEEP_ALIVE') or '5m').strip()}
            encoded = json.dumps(payload).encode()
            started = time.monotonic()
            with urllib.request.urlopen(urllib.request.Request(
                node['base_url'] + '/api/chat', data=encoded,
                headers={'Content-Type': 'application/json'}), timeout=90) as response:
                value = json.load(response)
            content = value.get('message', {}).get('content', '')
            emit({'node': node['id'], 'message_id': current_id, 'repeat': current_repeat,
                  'workload': kwargs.get('workload'), 'request_sha256': hashlib.sha256(encoded).hexdigest(),
                  'output_sha256': hashlib.sha256(content.encode()).hexdigest(),
                  'latency_s': round(time.monotonic()-started, 3),
                  **{key: value.get(key) for key in ('load_duration', 'eval_count', 'eval_duration', 'prompt_eval_count')}})
            return SimpleNamespace(content=content, model=model, duration_ms=int((time.monotonic()-started)*1000))

        original = agent.chat_structured
        agent.chat_structured = read_model
        try:
            for message, attachments in corpus:
                current_id = message['provider_message_id']
                for current_repeat in range(repeats):
                    result, model, duration = agent._analyze_on_one_node(message, attachments)
                    emit({'node': node['id'], 'message_id': current_id, 'repeat': current_repeat,
                          'phase': 'verdict', 'status': result.get('status'),
                          'relevance': {k: (result.get('recruitment_relevance_result') or {}).get(k)
                                        for k in ('decision', 'message_kind', 'confidence')},
                          'validation': result.get('backend_validation_reason'), 'duration_ms': duration,
                          'model_validation': result.get('model_validation')})
        except Exception as exc:
            emit({'node': node['id'], 'phase': 'aborted', 'error_type': type(exc).__name__})
            # Never pile up work behind a timed-out production model.
        finally:
            agent.chat_structured = original
        emit({'node': node['id'], 'phase': 'after', 'ps': get('/api/ps')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message-id', action='append', required=True)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    run(args.message_id, args.repeats)

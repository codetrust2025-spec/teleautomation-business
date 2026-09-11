"""The real validator/worker boundary never parks an unresolved mail for a person."""
import json
from types import SimpleNamespace

import pytest

from services import recruitment_mail_agent as agent
from services import recruitment_automation as automation
from services.recruitment_semantics import classify_context
from tests.test_recruitment_pipeline import message


@pytest.mark.parametrize('reason', [
    'AI_CONFIDENCE_BELOW_THRESHOLD', 'MODEL_DISAGREEMENT', 'EVIDENCE_NOT_VERBATIM',
    'SOURCE_ASSERTS_TRANSITION_UNQUOTED', 'PROPOSAL_NOT_CORROBORATED', 'OLLAMA_UNAVAILABLE',
])
def test_process_message_durably_retries_every_unresolved_exit(monkeypatch, reason):
    statuses, analyses = [], []
    monkeypatch.setattr(agent, 'routing_decision', lambda *a: {'send_to_ai': True, 'score': 1, 'context': {}})
    monkeypatch.setattr(agent, '_publish', lambda *a, **kw: None)
    monkeypatch.setattr(agent.store, 'insert_message', lambda *a: ({'id': 'source'}, True))
    monkeypatch.setattr(agent.store, 'is_duplicate_content', lambda *a: False)
    monkeypatch.setattr(agent.store, 'is_duplicate_offer_attachment', lambda *a: False)
    monkeypatch.setattr(agent.store, 'mark_message_status', lambda *a, **kw: statuses.append((a, kw)))
    monkeypatch.setattr(agent.store, 'record_analysis', lambda *a, **kw: analyses.append((a, kw)))
    monkeypatch.setattr(agent.store, 'create_event', lambda *a, **kw: pytest.fail('Unverified event was persisted'))
    from services import interview_auto_booking
    monkeypatch.setattr(interview_auto_booking, 'execute_auto_booking', lambda **kw: pytest.fail('Unverified mail booked'))
    monkeypatch.setattr(agent, 'analyze', lambda *a: ({
        'status': 'MANUAL_REVIEW_REQUIRED', 'primary_status': 'MANUAL_REVIEW_REQUIRED',
        'classification_source': 'OLLAMA', 'classification': 'needs_review',
        'requires_manual_review': True, 'backend_validation_reason': reason,
    }, 'test-model', 1))
    assert agent.process_message({'id': 'mailbox', 'candidate_id': 'candidate'}, message()) is None
    assert statuses[-1][0] == ('source', 'AI_RETRY_PENDING')
    assert statuses[-1][1]['reason'] == reason
    assert analyses[-1][1]['processing_status'] == 'RETRY_PENDING'
    assert analyses[-1][0][2]['requires_manual_review'] is False


@pytest.mark.parametrize('kind,confidence,quote,expected', [
    ('UNKNOWN', 97, 'Your interview', 'AI_RETRY_PENDING'),
    ('RECIPIENT_HIRING_PROCESS', 97, 'Your interview', 'AI_RETRY_PENDING'),
    ('JOB_ADVERTISEMENT', 40, 'Your interview', 'AI_RETRY_PENDING'),
    ('MARKETING_OR_TRAINING', 97, 'fabricated quote', 'AI_RETRY_PENDING'),
    ('JOB_ADVERTISEMENT', 97, 'Your interview', 'IGNORED_NOT_OFFER_RELATED'),
    ('MARKETING_OR_TRAINING', 97, 'Your interview', 'IGNORED_NOT_OFFER_RELATED'),
])
def test_real_analyze_entrypoint_distinguishes_uncertainty_from_ignore(monkeypatch, kind, confidence, quote, expected):
    output = {'decision': 'NOT_ESTABLISHED', 'message_kind': kind, 'confidence': confidence,
              'evidence': [{'source': 'EMAIL_BODY', 'text': quote}], 'reason': 'Corpus test'}
    monkeypatch.setattr(agent, 'chat_structured', lambda **kw: SimpleNamespace(
        content=json.dumps(output), model='test-model', duration_ms=1))
    result, _, _ = agent.analyze(message('Interview', 'Your interview details'))
    assert result['status'] == expected
    assert result['backend_transition_validated'] is False
    assert result['requires_manual_review'] is False


RISEBIRD_LINK = 'Interview Meeting Link: https://portal.risebird.io/Candidate/InterviewDetails?id=example'
RISEBIRD_BODY = ('This is to remind you that your interview is scheduled for '
                 '9/10/2026 5:00:00 PM(Timezone: IST). Job Description: View detailed job information. ')


def test_real_risebird_reminder_shape_is_not_a_job_ad():
    result = classify_context('Mphasis India: Interview Reminder Confirm Your Availability',
                              RISEBIRD_BODY + RISEBIRD_LINK, sender_email='notification@risebird.io')
    assert result['is_promotional_or_job_ad'] is False
    assert result['interview_event'] == 'INTERVIEW_CONFIRMED'


@pytest.mark.parametrize('subject,body', [
    ('Job description', 'Job Description: Java engineer. Apply now.'),
    ('Interview workshop', RISEBIRD_BODY + RISEBIRD_LINK),
    ('Interview preparation newsletter', RISEBIRD_BODY + RISEBIRD_LINK),
    ('Interview Reminder', 'Job description: technical interview rounds. ' + RISEBIRD_LINK),
    ('Interview Reminder', RISEBIRD_BODY + RISEBIRD_LINK + ' We are hiring. Apply now.'),
])
def test_the_risebird_exception_does_not_remove_ad_filters(subject, body):
    result = classify_context(subject, body, sender_email='notification@risebird.io')
    assert result['is_promotional_or_job_ad'] is True
    assert result['interview_event'] != 'INTERVIEW_CONFIRMED'


def test_normalization_cannot_approve_fabricated_evidence():
    value = {'status': 'MANUAL_REVIEW_REQUIRED', 'backend_transition_validated': False,
             'requires_manual_review': True, 'automation_decision': 'AUTO_BOOK'}
    automation.normalize_analysis(value)
    assert value['automation_decision'] == 'AI_RETRY_PENDING'
    assert value['backend_transition_validated'] is False

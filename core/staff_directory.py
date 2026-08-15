"""Central staff directory.

Real staff names, personal phone numbers and WhatsApp links must never live in
source. They are loaded at runtime from an external, git-ignored JSON file
(default ``config/staff_directory.json``; override with ``STAFF_DIRECTORY_FILE``).

The committed source and ``config/staff_directory.example.json`` contain
placeholders only. When the real config file is present, every accessor returns
the production values, so application behaviour is identical; when it is absent
(e.g. a fresh clone / CI) the placeholders keep the app importable and running.

Members are keyed by role slug so no real name appears in source:
    persona          – the auto-reply persona identity
    senior_tech      – senior software engineer (Java / AI / interview-dev)
    data_lead        – senior data analyst (Power BI / data) — pricing owner
    react_lead_a     – React / frontend senior
    react_lead_b     – React / frontend senior
    devops_lead      – DevOps / Cloud senior
    operator_extra   – additional operator number (human-operator detection)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_REL = "config/staff_directory.json"

# Placeholders only — NEVER put real names/numbers here.
_PLACEHOLDER: dict = {
    "persona_name": "STAFF_MEMBER_0",
    "members": {
        "senior_tech":    {"name": "STAFF_MEMBER_1", "phone": "9000000001",      "whatsapp": "https://wa.me/919000000001"},
        "data_lead":      {"name": "STAFF_MEMBER_2", "phone": "+91 90000 00002", "whatsapp": "https://wa.me/919000000002"},
        "react_lead_a":   {"name": "STAFF_MEMBER_3", "phone": "+91 90000 00003", "whatsapp": "https://wa.me/919000000003"},
        "react_lead_b":   {"name": "STAFF_MEMBER_4", "phone": "+91 90000 00004", "whatsapp": "https://wa.me/919000000004"},
        "devops_lead":    {"name": "STAFF_MEMBER_5", "phone": "+91 90000 00005", "whatsapp": "https://wa.me/919000000005"},
        "operator_extra": {"name": "STAFF_MEMBER_6", "phone": "9000000006",      "whatsapp": "https://wa.me/919000000006"},
    },
}


def _load() -> dict:
    rel = os.environ.get("STAFF_DIRECTORY_FILE", _DEFAULT_REL)
    p = Path(rel)
    if not p.is_absolute():
        p = _REPO / rel
    if p.is_file():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return _PLACEHOLDER


_DIR = _load()


def reload() -> None:
    """Reload the directory from disk (used by tests / after config changes)."""
    global _DIR
    _DIR = _load()


def persona_name() -> str:
    return str(_DIR.get("persona_name") or _PLACEHOLDER["persona_name"])


def member(slug: str) -> dict:
    base = _PLACEHOLDER["members"].get(slug, {})
    override = (_DIR.get("members") or {}).get(slug, {})
    return {**base, **override}


def name(slug: str) -> str:
    return str(member(slug).get("name") or "")


def phone(slug: str) -> str:
    return str(member(slug).get("phone") or "")


def whatsapp(slug: str) -> str:
    return str(member(slug).get("whatsapp") or "")


def phone_digits(slug: str) -> str:
    return re.sub(r"\D", "", phone(slug))[-10:]

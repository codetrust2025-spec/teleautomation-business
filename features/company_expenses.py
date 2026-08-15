"""Company-level expense ledger — tracks operational costs separate from
handler payouts (rent, tools, subscriptions, marketing, etc.).

Combined with handler_expenses, this gives a full picture of total
company expenditure.

Schema (one row):

    {
        "id":         "short id",
        "title":      "Expense description",
        "amount":     <int rupees>,
        "category":   "rent | tools | marketing | salary_staff | travel |
                       internet | office | subscription | other",
        "note":       "free text",
        "date":       "YYYY-MM-DD",
        "recurring":  false,
        "created_at": ISO ts,
        "updated_at": ISO ts,
    }
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

from core.config import DATA_DIR

_FILE = os.path.join(DATA_DIR, "company_expenses.json")
_lock = Lock()

VALID_CATEGORIES = {
    "rent", "tools", "marketing", "salary_staff", "travel",
    "internet", "office", "subscription", "other",
}

CATEGORY_LABELS = {
    "rent":          "Rent / space",
    "tools":         "Tools / equipment",
    "marketing":     "Marketing / ads",
    "salary_staff":  "Staff salary (non-handler)",
    "travel":        "Travel / fuel",
    "internet":      "Internet / telecom",
    "office":        "Office supplies",
    "subscription":  "Subscriptions / SaaS",
    "other":         "Other",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _empty() -> dict:
    return {"expenses": [], "updated_at": None}


def _load() -> dict:
    with _lock:
        if not os.path.exists(_FILE):
            return _empty()
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _empty()
            data.setdefault("expenses", [])
            data.setdefault("updated_at", None)
            return data
        except (OSError, json.JSONDecodeError):
            return _empty()


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    data["updated_at"] = _now_iso()
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


def _coerce_amount(value) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(0, int(float(str(value).replace(",", "").strip())))
    except (ValueError, TypeError):
        return 0


def _clean_str(value, *, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s or default


def _row_month(row: dict) -> str:
    raw = (row.get("date") or "").strip()
    if not raw:
        return ""
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    return ""


_ALLOWED_FIELDS = {"title", "amount", "category", "note", "date", "recurring"}


def _normalise(record: dict, *, existing: dict | None = None) -> dict:
    base = dict(existing) if existing else {}
    category = _clean_str(
        record.get("category", base.get("category", "other"))
    ).lower().replace(" ", "_").replace("-", "_")
    if category not in VALID_CATEGORIES:
        category = "other"
    out = {
        "id":         base.get("id") or _new_id(),
        "title":      _clean_str(record.get("title", base.get("title")))[:200],
        "amount":     _coerce_amount(record.get("amount", base.get("amount"))),
        "category":   category,
        "note":       _clean_str(record.get("note", base.get("note")))[:240],
        "date":       _clean_str(record.get("date", base.get("date"))),
        "recurring":  bool(record.get("recurring", base.get("recurring", False))),
        "created_at": base.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
    }
    return out


# ── Public API ──────────────────────────────────────────────────────────────

def list_expenses(*, month: str | None = None, category: str | None = None) -> list[dict]:
    """Most-recent first. Filter by month ('YYYY-MM') and/or category."""
    rows = list((_load().get("expenses") or []))
    if month and month != "all":
        rows = [r for r in rows if _row_month(r) == month]
    if category and category != "all":
        rows = [r for r in rows if r.get("category") == category]
    rows.sort(key=lambda r: (r.get("date") or "", r.get("created_at") or ""), reverse=True)
    return rows


def create_expense(record: dict) -> dict:
    data = _load()
    row = _normalise(record)
    data.setdefault("expenses", []).append(row)
    _save(data)
    return row


def update_expense(eid: str, patch: dict) -> dict | None:
    data = _load()
    rows = data.get("expenses") or []
    for i, r in enumerate(rows):
        if r.get("id") == eid:
            allowed = {k: v for k, v in patch.items() if k in _ALLOWED_FIELDS}
            rows[i] = _normalise(allowed, existing=r)
            data["expenses"] = rows
            _save(data)
            return rows[i]
    return None


def delete_expense(eid: str) -> bool:
    data = _load()
    before = data.get("expenses") or []
    after = [r for r in before if r.get("id") != eid]
    if len(after) == len(before):
        return False
    data["expenses"] = after
    _save(data)
    return True


def summary(*, month: str | None = None) -> dict:
    """Return total and by-category breakdown."""
    rows = list_expenses(month=month)
    total = 0
    by_category: dict[str, int] = {}
    for r in rows:
        amt = int(r.get("amount") or 0)
        total += amt
        cat = r.get("category") or "other"
        by_category[cat] = by_category.get(cat, 0) + amt
    return {
        "total": total,
        "count": len(rows),
        "by_category": by_category,
    }


def available_months() -> list[dict]:
    """Return distinct months from all expenses."""
    rows = _load().get("expenses") or []
    months_set: set[str] = set()
    for r in rows:
        m = _row_month(r)
        if m:
            months_set.add(m)
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    out = []
    for m in sorted(months_set, reverse=True):
        try:
            year, mo = m.split("-")
            label = f"{month_names[int(mo) - 1]} {year}"
        except (ValueError, IndexError):
            label = m
        out.append({"value": m, "label": label})
    return out


def total_expenditure(*, month: str | None = None) -> dict:
    """Combined view: handler payouts + company expenses = total expenditure."""
    # Company operational expenses
    company = summary(month=month)

    # Handler payouts (from existing module)
    try:
        from features import handler_expenses as _he
        handler_summary = _he.summary_by_handler(
            month=month if month and month != "all" else None
        )
        handler_total = sum(int(v.get("total") or 0) for v in handler_summary.values())
        handler_count = sum(int(v.get("count") or 0) for v in handler_summary.values())
    except Exception:
        handler_total = 0
        handler_count = 0

    grand_total = company["total"] + handler_total

    return {
        "grand_total": grand_total,
        "handler_payouts": {
            "total": handler_total,
            "count": handler_count,
        },
        "company_expenses": {
            "total": company["total"],
            "count": company["count"],
            "by_category": company["by_category"],
        },
        "month": month or "all",
    }

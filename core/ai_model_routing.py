"""Central model routing policy for TeleAutomation AI workloads.

Feature modules must ask the AI gateway for a route instead of hard-coding
Ollama model names. Environment variables remain the deployment override.
"""

from __future__ import annotations

import logging
import os
import re


logger = logging.getLogger(__name__)


MODEL_ROUTES = {
    "recruitment_email_primary": ("OLLAMA_PRIMARY_MODEL", "qwen2.5:7b"),
    "recruitment_email_validator": ("AI_RECRUITMENT_VALIDATOR_MODEL", "qwen2.5:7b"),
    "recruitment_document_vision": ("OLLAMA_VISION_MODEL", "qwen3-vl:8b-instruct"),
}

AUXILIARY_MODEL_ROUTES = {
    "interview_screenshot_vision": ("OLLAMA_VISION_MODEL", "qwen3-vl:8b-instruct"),
    # Payments read two long identifiers whose exact digits decide the
    # authorisation, so this route is deliberately NOT bound to the shared
    # OLLAMA_VISION_MODEL: a global vision change made for resumes or invites
    # must not be able to silently downgrade payment reading. That is not
    # hypothetical - a small vision model was once set globally and returned
    # empty strings for a 22-digit transaction ID. The default here is present
    # on every Ollama node.
    "payment_screenshot_vision": ("OLLAMA_PAYMENT_VISION_MODEL", "qwen3-vl:8b-instruct"),
    "resume_vision": ("OLLAMA_VISION_MODEL", "qwen3-vl:8b-instruct"),
    "reasoning_text": ("OLLAMA_REASONING_MODEL", "qwen2.5:7b"),
}


#: Routes where the model decides something about a real candidate: whether a
#: mail is recruitment at all, what transition it evidences, and whether the
#: evidence entails it. Accuracy outranks latency on every one of them.
TEXT_DECISION_ROUTES = frozenset({
    "recruitment_email_primary",
    "recruitment_email_validator",
    "reasoning_text",
})

#: Smallest model allowed to make those decisions, in billions of parameters.
#: Production shipped with a stale AI_RECRUITMENT_MODEL=gemma2:2b sitting in the
#: env file: inert only because OLLAMA_PRIMARY_MODEL happened to outrank it, and
#: one unset variable away from a 2B model classifying interview mail. The floor
#: makes that unreachable rather than merely unlikely.
MIN_TEXT_DECISION_PARAMS_B = 7.0

#: Used when a configured model is refused. Present on every node.
SAFE_TEXT_DECISION_MODEL = "qwen2.5:7b"

# Ollama tags carry the size in the part after the colon: "7b", "8b-instruct",
# "0.5b", "14b-instruct-q4_K_M". Anything without one - "latest", a bare digest -
# is unjudgeable and is allowed through rather than breaking a deployment that
# names its model some other way.
_PARAMETER_SIZE_RE = re.compile(r"(?:^|[-_])(\d+(?:\.\d+)?)b(?:$|[-_])", re.IGNORECASE)


def model_parameter_billions(model: str) -> float | None:
    """Parameter count a tag advertises, or None when it does not say."""
    tag = str(model or "").strip()
    if ":" not in tag:
        return None
    match = _PARAMETER_SIZE_RE.search(tag.split(":", 1)[1])
    return float(match.group(1)) if match else None


def is_weak_text_model(model: str) -> bool:
    """Does this tag advertise a model too small to decide a candidate's mail?"""
    size = model_parameter_billions(model)
    return size is not None and size < MIN_TEXT_DECISION_PARAMS_B


def guard_text_decision_model(model: str, *, route: str) -> str:
    """Refuse a downgrade on a route that decides something about a candidate.

    Returns the model unchanged unless its tag advertises fewer parameters than
    the floor, in which case the safe default is substituted and the refusal is
    logged loudly. Speed is never a reason to answer these with a small model,
    so there is no override: a genuine upgrade names a larger model and passes.
    """
    candidate = str(model or "").strip()
    if not candidate or not is_weak_text_model(candidate):
        return candidate
    logger.error(
        "Refusing weak model for a mail decision route=%s configured=%s floor=%sB using=%s",
        route, candidate, MIN_TEXT_DECISION_PARAMS_B, SAFE_TEXT_DECISION_MODEL,
    )
    return SAFE_TEXT_DECISION_MODEL


def model_for(route: str) -> str:
    """Return the configured model for a named AI workload."""
    try:
        variable, default = {**MODEL_ROUTES, **AUXILIARY_MODEL_ROUTES}[route]
    except KeyError as exc:
        raise ValueError(f"Unknown AI model route: {route}") from exc
    legacy = (
        os.getenv("OLLAMA_MAIL_MODEL") or os.getenv("AI_RECRUITMENT_MODEL")
        if route == "recruitment_email_primary" else None
    )
    resolved = (os.getenv(variable) or legacy or default).strip()
    if route in TEXT_DECISION_ROUTES:
        return guard_text_decision_model(resolved, route=route)
    return resolved


def configured_model_routes() -> dict[str, str]:
    return {route: model_for(route) for route in MODEL_ROUTES}

"""Central model routing policy for TeleAutomation AI workloads.

Feature modules must ask the AI gateway for a route instead of hard-coding
Ollama model names. Environment variables remain the deployment override.
"""

from __future__ import annotations

import os


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
    return (os.getenv(variable) or legacy or default).strip()


def configured_model_routes() -> dict[str, str]:
    return {route: model_for(route) for route in MODEL_ROUTES}

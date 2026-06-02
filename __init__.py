"""OP SMS platform plugin for Hermes Agent."""

from __future__ import annotations

from pathlib import Path

try:  # Normal Hermes plugin package import path.
    from .adapter import OPAdapter, register as _register_platform
    from .op_client import (
        compute_webhook_signature,
        format_op_error,
        parse_signature_header,
        verify_webhook_signature,
    )
except ImportError:  # Pytest may import repo-root __init__.py as a top-level module.
    from adapter import OPAdapter, register as _register_platform  # type: ignore
    from op_client import (  # type: ignore
        compute_webhook_signature,
        format_op_error,
        parse_signature_header,
        verify_webhook_signature,
    )


def register(ctx) -> None:
    """Plugin entry point called by Hermes."""
    _register_platform(ctx)
    skill_path = Path(__file__).parent / "skills" / "op-sms" / "SKILL.md"
    if skill_path.exists() and hasattr(ctx, "register_skill"):
        ctx.register_skill(
            "op-sms",
            skill_path,
            description="Set up and troubleshoot OP phone numbers as a Hermes SMS channel.",
        )


__all__ = [
    "OPAdapter",
    "register",
    "compute_webhook_signature",
    "format_op_error",
    "parse_signature_header",
    "verify_webhook_signature",
]

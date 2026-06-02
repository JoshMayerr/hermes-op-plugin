"""OP SMS platform plugin for Hermes Agent."""

from __future__ import annotations

from pathlib import Path

try:  # Normal Hermes plugin package import path.
    from .adapter import OPAdapter, register as _register_platform
    from .op_cli import register_cli as _register_cli
    from .op_tools import register_tools as _register_tools
    from .op_client import (
        compute_webhook_signature,
        format_op_error,
        parse_signature_header,
        verify_webhook_signature,
    )
except ImportError:  # Pytest may import repo-root __init__.py as a top-level module.
    from adapter import OPAdapter, register as _register_platform  # type: ignore
    from op_cli import register_cli as _register_cli  # type: ignore
    from op_tools import register_tools as _register_tools  # type: ignore
    from op_client import (  # type: ignore
        compute_webhook_signature,
        format_op_error,
        parse_signature_header,
        verify_webhook_signature,
    )


def _register_skills(ctx) -> None:
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.exists() or not hasattr(ctx, "register_skill"):
        return
    descriptions = {
        "op-sms": "Set up and troubleshoot OP phone numbers as a Hermes SMS channel.",
        "op-api": "Use OP API tools for SMS, numbers, API keys, and dashboardless setup.",
    }
    for child in sorted(skills_dir.iterdir()):
        skill_path = child / "SKILL.md"
        if child.is_dir() and skill_path.exists():
            ctx.register_skill(child.name, skill_path, description=descriptions.get(child.name, "OP integration skill."))


def register(ctx) -> None:
    """Plugin entry point called by Hermes."""
    _register_platform(ctx)
    _register_tools(ctx)
    _register_cli(ctx)
    _register_skills(ctx)


__all__ = [
    "OPAdapter",
    "register",
    "compute_webhook_signature",
    "format_op_error",
    "parse_signature_header",
    "verify_webhook_signature",
]

"""Paranoy native Hermes plugin. Structure only: register, do not sign or call out."""

from __future__ import annotations

import logging
from pathlib import Path

from . import gates, schemas, signing
from .tools import governance, read, write

logger = logging.getLogger(__name__)

_TOOL_GROUPS = (read.TOOLS, write.TOOLS, governance.TOOLS)


def pre_llm_call(**kwargs):
    """No-op. Do not log the user message or any secret."""
    del kwargs
    return None


def post_tool_call(**kwargs):
    """No-op. Do not log tool arguments or results; they might carry a secret."""
    del kwargs
    return None


def register(ctx):
    """Register skills, slash and CLI stubs, hooks, and gated tools.

    A missing ``PARANOY_BOT_SECRET_HEX`` does not stop this function. Data
    tools stay hidden until ``check_fn`` sees a key and, where the mode
    needs one, a collection.
    """
    skills_dir = Path(__file__).resolve().parent / "skills"
    descriptions = {
        "reader": "Read one pinned Paranoy collection. No writes.",
        "contributor": "Contribute to one pinned Paranoy collection in the assigned roles.",
        "admin": "Administer Paranoy collections this key owns or administers. No content writes.",
        "ledger": "Account-wide steward for the owner's Paranoy ledger.",
    }
    for skill_name in schemas.SKILLS:
        ctx.register_skill(
            skill_name,
            skills_dir / skill_name / "SKILL.md",
            description=descriptions[skill_name],
        )

    def slash(raw_args: str = "", **kwargs) -> str:
        del kwargs
        return gates.slash_result(gates.read_settings(ctx), raw_args)

    ctx.register_command(
        "paranoy",
        handler=slash,
        description="Paranoy status, collection, mode, roles, and whoami",
        args_hint="status|collection|mode|roles|whoami",
    )

    def cli(args) -> None:
        command = getattr(args, "paranoy_command", None)
        if command == "status":
            print(gates.status_text(gates.read_settings(ctx)))
            return
        if command == "keygen":
            print(
                "paranoy keygen is not implemented. A later version will "
                "generate a key and store it with Hermes as "
                f"{schemas.SECRET_ENV}. This command does not create or "
                "print a secret."
            )
            return
        print("Usage: hermes paranoy <status|keygen>")

    def setup_cli(subparser) -> None:
        subs = subparser.add_subparsers(dest="paranoy_command")
        subs.add_parser("status", help="Show mode, collection, and whether a key is configured")
        subs.add_parser("keygen", help="Generate an agent key through Hermes (not implemented)")
        subparser.set_defaults(func=cli)

    ctx.register_cli_command(
        name="paranoy",
        help="Paranoy ledger status and key setup",
        setup_fn=setup_cli,
        handler_fn=cli,
    )

    def pre_tool_call(**kwargs):
        return gates.pre_tool_directive(
            ctx, kwargs.get("tool_name"), kwargs.get("args")
        )

    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)

    def make_check(group: str, tool_name: str):
        def check_fn() -> bool:
            try:
                return gates.tool_visible(gates.read_settings(ctx), group, tool_name)
            except Exception:
                return False

        return check_fn

    for group_tools in _TOOL_GROUPS:
        for spec in group_tools:
            ctx.register_tool(
                name=spec["name"],
                toolset="paranoy",
                schema=spec["schema"],
                handler=spec["handler"],
                check_fn=make_check(spec["group"], spec["name"]),
            )

    logger.debug(
        "paranoy scaffold registered %d tools; signing is %s",
        len(schemas.ALL_TOOL_NAMES),
        signing.describe(),
    )

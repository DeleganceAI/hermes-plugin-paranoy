"""Paranoy native Hermes plugin. Signs on the agent's behalf."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import beliefs, capture, gates, schemas, state
from .client import ParanoyClient, PluginError
from .runtime import now_iso, register_account
from .signing import SecretError, generate_and_store, load_secret_hex
from .tools import config, governance, profile, read, write
from .tools import beliefs as belief_tools
from .tools import jobs as job_tools
from .tools.read import status as live_status

logger = logging.getLogger(__name__)
_NSEC = re.compile(r"nsec1[02-9ac-hj-np-z]+")


def register(ctx):
    """Register skills, commands, hooks, and tools.

    A missing PARANOY_BOT_SECRET_HEX does not stop registration. Data tools
    stay hidden until a key is stored and, where the mode needs one, a
    collection is pinned.
    """
    skills_dir = Path(__file__).resolve().parent / "skills"
    descriptions = {
        "reader": "Read one pinned Paranoy collection. No writes.",
        "contributor": "Contribute to one pinned Paranoy collection in the assigned roles.",
        "admin": "Administer Paranoy collections this key owns or administers. No content writes.",
        "ledger": "Account-wide steward for the owner's Paranoy ledger.",
        "setup": (
            "Set up this Paranoy agent: npub and status, getting added to a collection, "
            "setting the profile name, and why the ledger is not visible."
        ),
        "cadence": (
            "Recurring Paranoy jobs as one role in one collection, for example an hourly "
            "Scout adding 50 items. Create, list, and remove them."
        ),
        "beliefs": (
            "Surface the user's beliefs from the current conversation and, after approval, "
            "track evidence for and against them in a private collection."
        ),
    }
    for skill_name in schemas.SKILLS:
        ctx.register_skill(
            skill_name,
            skills_dir / skill_name / "SKILL.md",
            description=descriptions[skill_name],
        )

    def settings_factory():
        return gates.read_settings(ctx)

    def service_factory(settings):
        try:
            secret_hex = load_secret_hex()
        except SecretError as exc:
            raise PluginError(str(exc)) from exc
        return ParanoyClient(settings["server_url"], secret_hex)

    def slash(raw_args: str = "", **kwargs) -> str:
        parts = (raw_args or "").split()
        command = parts[0].lower() if parts else "status"
        settings = settings_factory()
        if command == "status" and len(parts) == 1 and settings.get("key_configured"):
            try:
                payload = live_status(service_factory(settings), settings, {})
                return json.dumps(payload, ensure_ascii=False)
            except PluginError as exc:
                return gates.status_text(settings) + "\nserver: " + str(exc)
        # Every subcommand has an agent tool too; see schemas.SLASH_TOOLS.
        return gates.slash_result(
            ctx, raw_args, service_factory, session_id=kwargs.get("session_id") or ""
        )

    ctx.register_command(
        "paranoy",
        handler=slash,
        description="Paranoy status, collection, mode, roles, whoami, profile, jobs, and beliefs",
        args_hint="status|collection|mode|roles|whoami|profile [name|about|publish]|job [create|list|remove]|beliefs [scan|approve]",
    )

    def keygen() -> str | None:
        """Make and store a key; return its hex only to this process, for register's first run."""
        stored = {}

        def save(key, value):
            from hermes_cli.config import save_env_value

            save_env_value(key, value)
            stored["hex"] = value

        print(generate_and_store(save))
        return stored.get("hex")

    def cli(args) -> None:
        command = getattr(args, "paranoy_command", None)
        if command == "status":
            print(slash("status"))
            return
        if command == "keygen":
            keygen()
            return
        if command == "register":
            settings = settings_factory()
            try:
                if settings.get("key_configured"):
                    service = service_factory(settings)
                else:  # first run makes the key, so the owner types one command
                    secret_hex = keygen()
                    if not secret_hex:
                        return
                    service = ParanoyClient(settings["server_url"], secret_hex)
                print(register_account(service, settings, getattr(args, "email", None)))
            except PluginError as exc:
                print(f"Registration failed: {exc}")
            return
        if command == "profile":
            extra = " ".join(getattr(args, "profile_args", None) or [])
            print(slash(("profile " + extra).strip()))
            return
        print("Usage: hermes paranoy <status|keygen|register [email]|profile>")

    def setup_cli(subparser) -> None:
        subs = subparser.add_subparsers(dest="paranoy_command")
        subs.add_parser("status", help="Show mode, npub, and whether a key is configured")
        subs.add_parser("keygen", help="Generate an agent key and store it with Hermes")
        register_parser = subs.add_parser(
            "register",
            help="Create this agent's Paranoy account; with your email, you hold it and the agent is its Ledger",
        )
        register_parser.add_argument("email", nargs="?")
        profile_parser = subs.add_parser(
            "profile",
            help="Show, set, or publish the agent profile: [name <text>|about <text>|publish]",
        )
        profile_parser.add_argument("profile_args", nargs="*")
        subparser.set_defaults(func=cli)

    ctx.register_cli_command(
        name="paranoy",
        help="Paranoy ledger status and key setup",
        setup_fn=setup_cli,
        handler_fn=cli,
    )

    def pre_llm_call(**kwargs):
        # Terminal-mode evidence capture: the gateway path handles messaging
        # platforms; pre_llm_call fires once per turn on every surface, so
        # the strictly-terminal check inside keeps the two paths disjoint.
        try:
            payload = capture.terminal_payload(kwargs, ctx, settings_factory)
            if payload is not None:
                capture.start_inbound_thread(payload, ctx, settings_factory, service_factory)
        except Exception:
            pass
        try:
            beliefs.remember_turn(kwargs)
        except Exception:
            logger.debug("beliefs turn was not cached")
        try:
            return {"context": gates.prompt_context(settings_factory())}
        except Exception:
            return None

    def subagent_start(**kwargs):
        capture.note_subagent_started(kwargs.get("child_session_id"))
        return None

    def subagent_stop(**kwargs):
        capture.note_subagent_stopped(kwargs.get("child_session_id"))
        return None

    def pre_tool_call(**kwargs):
        return gates.pre_tool_directive(ctx, kwargs.get("tool_name"), kwargs.get("args"))

    def post_tool_call(**kwargs):
        name = gates.bare_tool_name(kwargs.get("tool_name"))
        if name not in schemas.ALL_TOOL_NAMES:
            return None
        raw = kwargs.get("result")
        if not isinstance(raw, str):
            return None
        cleaned = _NSEC.sub("[redacted]", raw)
        try:
            payload = json.loads(cleaned)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("error"):
            return None
        accepted = payload.get("accepted")
        if isinstance(accepted, list) and accepted:
            state.append_jsonl(
                "accepted.jsonl",
                {"tool": name, "ids": accepted, "at": now_iso()},
            )
        try:
            settings = settings_factory()
        except Exception:
            settings = {}
        role = settings.get("active_role") or settings.get("mode") or "none"
        state.checkpoint(
            role,
            {
                "tool": name,
                "at": now_iso(),
                "collection": settings.get("collection") or "",
                "overview": name == "paranoy_update_overview",
            },
        )
        return None

    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)

    def post_gateway_admission(**kwargs):
        """Capture a message Hermes has already admitted. Return None so the turn runs.

        The hook receives ``source``, ``text``, and ``message_id`` (plus
        ``platform`` and ``session_key``). It must not return
        ``{"action": "handled"}``.
        """
        try:
            payload = capture.inbound_payload(kwargs, ctx, settings_factory)
            if payload is not None:
                capture.start_inbound_thread(payload, ctx, settings_factory, service_factory)
        except Exception:
            logger.warning("paranoy capture: post_gateway_admission failed", exc_info=True)
        return None

    ctx.register_hook("post_gateway_admission", post_gateway_admission)
    ctx.register_hook("subagent_start", subagent_start)
    ctx.register_hook("subagent_stop", subagent_stop)

    def llm_execution_middleware(**kwargs):
        return capture.llm_execution(
            ctx=ctx,
            settings_factory=settings_factory,
            service_factory=service_factory,
            **kwargs,
        )

    ctx.register_middleware("llm_execution", llm_execution_middleware)

    def make_check(tool_name):
        def check_fn() -> bool:
            try:
                return gates.tool_visible(settings_factory(), tool_name)
            except Exception:
                return False

        return check_fn

    def save_setting(key, value):
        gates.save_setting(ctx, key, value)

    for spec in (
        *read.build(service_factory, settings_factory),
        *write.build(service_factory, settings_factory),
        *governance.build(service_factory, settings_factory),
        *profile.build(service_factory, settings_factory, save_setting),
        *config.build(service_factory, settings_factory, save_setting),
        *job_tools.build(service_factory, settings_factory),
        *belief_tools.build(service_factory, settings_factory),
    ):
        ctx.register_tool(
            name=spec["name"],
            toolset="paranoy",
            schema=spec["schema"],
            handler=spec["handler"],
            check_fn=make_check(spec["name"]),
        )

    logger.debug("paranoy registered %d tools", len(schemas.ALL_TOOL_NAMES))

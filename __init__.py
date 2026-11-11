"""Paranoy native Hermes plugin. Signs on the agent's behalf."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import gates, public, schemas, state
from .client import ParanoyClient, PluginError
from .runtime import now_iso
from .signing import SecretError, load_secret_hex
from .tools import config, governance, profile, read, write
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
        del kwargs
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
        return gates.slash_result(ctx, raw_args, service_factory)

    ctx.register_command(
        "paranoy",
        handler=slash,
        description="Paranoy status, public collections, collection, mode, roles, whoami, profile, and jobs",
        args_hint="status|public|collection|mode|roles|whoami|profile [name|about|publish]|job [create|list|remove]",
    )

    def _server_url() -> str:
        try:
            return str(ctx.get_config("server_url", schemas.DEFAULTS["server_url"]) or schemas.DEFAULTS["server_url"])
        except Exception:
            return schemas.DEFAULTS["server_url"]

    def _registration_config():
        try:
            return ctx.get_config("register_public_collections", schemas.DEFAULTS["register_public_collections"])
        except Exception:
            return schemas.DEFAULTS["register_public_collections"]

    def cli(args) -> None:
        from .public import keygen_from_settings, register_stored, format_registration

        command = getattr(args, "paranoy_command", None)
        if command == "status":
            print(slash("status"))
            return
        if command in {"keygen", "register"}:
            from .client import PluginError
            from .signing import SecretError, load_secret_hex

            def save(key, value):
                from hermes_cli.config import save_env_value

                save_env_value(key, value)

            cli_value = getattr(args, "collections", None)
            try:
                if command == "keygen":
                    print(
                        keygen_from_settings(
                            save,
                            server_url=_server_url(),
                            cli_value=cli_value,
                            config_value=_registration_config(),
                        )
                    )
                    return
                try:
                    secret_hex = load_secret_hex()
                except SecretError as exc:
                    print(str(exc))
                    return
                from .public import resolve_choice

                choice = resolve_choice(cli_value, _registration_config())
                report = register_stored(_server_url(), secret_hex, choice)
                print(format_registration(report, secret_hex=secret_hex))
                return
            except PluginError as exc:
                print(str(exc))
                return
        if command == "profile":
            extra = " ".join(getattr(args, "profile_args", None) or [])
            print(slash(("profile " + extra).strip()))
            return
        print("Usage: hermes paranoy <status|keygen|register|profile>")

    def setup_cli(subparser) -> None:
        subs = subparser.add_subparsers(dest="paranoy_command")
        subs.add_parser("status", help="Show mode, npub, and whether a key is configured")
        keygen_parser = subs.add_parser(
            "keygen",
            help="Generate an agent key, store it with Hermes, and register it with public collections",
        )
        register_parser = subs.add_parser(
            "register",
            help="Register the stored key with public collections. Does not replace the key",
        )
        for parser in (keygen_parser, register_parser):
            parser.add_argument(
                "--collections",
                default=None,
                help=(
                    'Public collections to register with: "all" (default), "none", '
                    "or a comma-separated slug list. Overrides register_public_collections."
                ),
            )
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
        del kwargs
        try:
            return {"context": gates.prompt_context(settings_factory())}
        except Exception:
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

    def make_check(tool_name):
        def check_fn() -> bool:
            try:
                return gates.tool_visible(settings_factory(), tool_name)
            except Exception:
                return False

        return check_fn

    def save_setting(key, value):
        ctx.set_config(key, value)

    for spec in (
        *read.build(service_factory, settings_factory),
        *write.build(service_factory, settings_factory),
        *governance.build(service_factory, settings_factory),
        *profile.build(service_factory, settings_factory, save_setting),
        *config.build(service_factory, settings_factory, save_setting),
        *job_tools.build(service_factory, settings_factory),
    ):
        ctx.register_tool(
            name=spec["name"],
            toolset="paranoy",
            schema=spec["schema"],
            handler=spec["handler"],
            check_fn=make_check(spec["name"]),
        )

    logger.debug("paranoy registered %d tools", len(schemas.ALL_TOOL_NAMES))

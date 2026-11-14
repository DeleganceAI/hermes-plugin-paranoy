"""Paranoy native Hermes plugin: capture, ground, five tools, and /decide. See SPEC.md."""

from __future__ import annotations

import logging
from pathlib import Path

from . import capture, client, ground, ledger, settings, signing, state, tools
from .client import ParanoyClient, PluginError
from .signing import SecretError, generate_and_store, load_secret_hex

__all__ = ["capture", "client", "ground", "ledger", "register", "settings", "signing", "state", "tools"]

logger = logging.getLogger(__name__)

USAGE = "Usage: /paranoy [status | collection <slug> | capture on|off | ground on|off]"


def status_text(current: dict, service_factory) -> str:
    lines = [f"server: {current['server_url']}"]
    if not current["key_configured"]:
        return "\n".join(lines + ["key: none. Run `hermes paranoy register <email>` in a terminal."])
    try:
        service = service_factory(current)
        lines.append(f"npub: {service.npub}")
        rows = service.call("list_collections", {}).get("result") or []
        slugs = [row.get("slug") for row in rows if isinstance(row, dict)]
    except PluginError as exc:
        return "\n".join(lines + [f"server said: {exc}"])
    collection = current["collection"]
    if not collection:
        lines.append("collection: none. Run /paranoy collection <slug>. This key can use: " + (", ".join(slugs) or "none yet"))
    elif collection in slugs:
        lines.append(f"collection: {collection}")
    else:
        lines.append(f"collection: {collection} (this key is not a member; a new slug is created on the first write)")
    lines.append(f"capture: {'on' if current['capture'] else 'off'}")
    lines.append(f"ground: {'on' if current['ground'] else 'off'}")
    return "\n".join(lines)


def register(ctx):
    """Register the skills, commands, hooks, and tools. A missing key does not stop this."""
    skills_dir = Path(__file__).resolve().parent / "skills"
    for name, description in (
        ("decide", "The Paranoy-grounded decision procedure that /decide runs."),
        ("setup", "Set up this Paranoy agent: key, account, collection, and common errors."),
    ):
        ctx.register_skill(name, skills_dir / name / "SKILL.md", description=description)

    def settings_factory() -> dict:
        return settings.read(ctx)

    def service_factory(current: dict) -> ParanoyClient:
        try:
            return ParanoyClient(current["server_url"], load_secret_hex())
        except SecretError as exc:
            raise PluginError(str(exc)) from exc

    # ------------------------------------------------------------- commands

    def never_raises(handler):
        def safe(raw_args: str = "", **kwargs) -> str:
            try:
                return handler(raw_args, **kwargs)
            except PluginError as exc:
                return str(exc)
            except Exception:
                logger.exception("paranoy command failed")
                return "The Paranoy command failed unexpectedly."

        return safe

    @never_raises
    def paranoy(raw_args: str = "", **kwargs) -> str:
        parts = (raw_args or "").split()
        command = parts[0].lower() if parts else "status"
        value = parts[1].lower() if len(parts) > 1 else ""
        if command == "status":
            return status_text(settings_factory(), service_factory)
        if command == "collection" and settings.SLUG.fullmatch(value):
            settings.save(ctx, "collection", value)
            return status_text(settings_factory(), service_factory)
        if command in ("capture", "ground") and value in ("on", "off"):
            settings.save(ctx, command, value == "on")
            return f"{command}: {value}"
        return USAGE

    @never_raises
    def decide(raw_args: str = "", **kwargs) -> str:
        return ground.decide(raw_args, str(kwargs.get("session_id") or ""), settings_factory(), service_factory)

    ctx.register_command("paranoy", handler=paranoy, description="Paranoy status and settings",
                         args_hint="status|collection <slug>|capture on|off|ground on|off")
    ctx.register_command("decide", handler=decide, description="Make a decision grounded in the Paranoy ledger",
                         args_hint="<question>|done")

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
            print(paranoy("status"))
        elif command == "keygen":
            keygen()
        elif command == "register":
            current = settings_factory()
            try:
                if current["key_configured"]:
                    service = service_factory(current)
                else:  # first run makes the key, so the owner types one command
                    secret_hex = keygen()
                    if not secret_hex:
                        return
                    service = ParanoyClient(current["server_url"], secret_hex)
                print(ledger.register_account(service, current, getattr(args, "email", None)))
            except PluginError as exc:
                print(f"Registration failed: {exc}")
        else:
            print("Usage: hermes paranoy <status|keygen|register [email]>")

    def setup_cli(subparser) -> None:
        subs = subparser.add_subparsers(dest="paranoy_command")
        subs.add_parser("status", help="Show the npub, collection, and whether capture and grounding are on")
        subs.add_parser("keygen", help="Generate an agent key and store it with Hermes")
        subs.add_parser(
            "register", help="Create this agent's Paranoy account; with your email, you hold it"
        ).add_argument("email", nargs="?")
        subparser.set_defaults(func=cli)

    ctx.register_cli_command(name="paranoy", help="Paranoy status and key setup", setup_fn=setup_cli, handler_fn=cli)

    # ---------------------------------------------------------------- hooks

    def pre_llm_call(**kwargs):
        """Once per turn: capture a terminal message in the background, then ground the reply."""
        try:
            current = settings_factory()
            if current["ready"] and current["capture"]:
                payload = capture.terminal_payload(kwargs, ctx, current)
                if payload:
                    capture.start(payload, ctx, current, service_factory)
            block = ground.context(kwargs, ctx, current, service_factory)
            return {"context": block} if block else None
        except Exception:
            logger.warning("paranoy pre_llm_call failed", exc_info=True)
            return None

    def post_gateway_admission(**kwargs):
        """Capture a message Hermes has admitted. Returns None so the turn runs."""
        try:
            current = settings_factory()
            if current["ready"] and current["capture"]:
                payload = capture.gateway_payload(kwargs, current)
                if payload:
                    capture.start(payload, ctx, current, service_factory)
        except Exception:
            logger.warning("paranoy post_gateway_admission failed", exc_info=True)

    def transform_llm_output(**kwargs):
        return ground.append_warnings(kwargs.get("response_text"), kwargs.get("session_id"))

    for name, hook in (
        ("pre_llm_call", pre_llm_call),
        ("post_gateway_admission", post_gateway_admission),
        ("transform_llm_output", transform_llm_output),
        ("subagent_start", lambda **kw: capture.note_subagent_started(kw.get("child_session_id"))),
        ("subagent_stop", lambda **kw: capture.note_subagent_stopped(kw.get("child_session_id"))),
    ):
        ctx.register_hook(name, hook)

    # ---------------------------------------------------------------- tools

    def ready() -> bool:
        try:
            return settings_factory()["ready"]
        except Exception:
            return False

    for spec in tools.build(ctx, settings_factory, service_factory):
        ctx.register_tool(name=spec["name"], toolset="paranoy", schema=spec["schema"],
                          handler=spec["handler"], check_fn=ready)

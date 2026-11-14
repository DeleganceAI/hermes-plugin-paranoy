"""Load the plugin package and a fake Paranoy service for unit tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    name = "paranoy_plugin"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = name
    module.__path__ = [str(ROOT)]
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeService:
    def __init__(self, plugin, responses=None, secret_hex=None):
        secret_hex = secret_hex or ("11" * 32)
        self.signer = plugin.signing.Signer(
            secret_hex,
            clock=lambda: 1_700_000_000,
            aux_rand=lambda: b"\x22" * 32,
        )
        self.responses = responses or {}
        self.calls = []
        self.published = []
        self.npub = self.signer.npub
        self.pubkey = self.signer.pubkey

    def sign(self, kind, content, tags=None):
        return self.signer.sign_event(kind, content, tags=tags)

    def call(self, tool, args=None):
        self.calls.append((tool, dict(args or {})))
        found = self.responses.get(tool, {})
        if callable(found):
            return found(args or {})
        return found

    def publish(self, events):
        self.published.append(events)
        return {
            "accepted": [event["id"] for event in events],
            "skipped": [],
            "collections": [],
        }


class FakeLlm:
    """Stands in for ctx.llm. ``extraction`` answers the gate and claims call,
    ``matches`` answers the same-claim call."""

    def __init__(self, extraction=None, matches=None):
        self.extraction = extraction or {"evidence": False, "claims": []}
        self.matches = matches or []
        self.calls = []

    def complete_structured(self, instructions, input, **kwargs):
        self.calls.append(instructions)
        same = "candidate" in instructions
        parsed = {"matches": self.matches} if same else self.extraction
        return type("Result", (), {"parsed": parsed, "text": ""})()


class FakeCtx:
    def __init__(self, llm=None, config=None):
        self.llm = llm
        self.config = dict(config or {})
        self.tools, self.hooks, self.commands, self.skills = {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value

    def register_tool(self, name, toolset, schema, handler, check_fn=None):
        self.tools[name] = {"schema": schema, "handler": handler, "check_fn": check_fn}

    def register_hook(self, name, hook):
        self.hooks[name] = hook

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler

    def register_skill(self, name, path, **kwargs):
        self.skills[name] = path

    def register_cli_command(self, **kwargs):
        pass


def settings(**changes):
    base = {
        "server_url": "https://paranoy.test", "collection": "notes", "owner_name": "Dustin",
        "capture": True, "ground": True, "agent_name": "", "agent_about": "", "permalink_base": "",
        "terms_version": "1", "terms_sha256": "ab", "key_configured": True, "ready": True,
    }
    return {**base, **changes}

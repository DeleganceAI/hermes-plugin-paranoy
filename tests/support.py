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

"""HTTPS client for ``{server_url}/mcp``. The secret never leaves the signer."""

from __future__ import annotations

import json
import re

import httpx

from .signing import Signer

_NSEC = re.compile(r"nsec1[02-9ac-hj-np-z]+")


class PluginError(Exception):
    """A failure whose message is safe to show to the model."""


def redact(text: str, secret_hex: str | None = None) -> str:
    cleaned = str(text or "")
    if secret_hex:
        cleaned = cleaned.replace(secret_hex, "[redacted]")
        cleaned = cleaned.replace(secret_hex.upper(), "[redacted]")
    return _NSEC.sub("[redacted]", cleaned)


def mcp_url(server_url: str) -> str:
    base = str(server_url or "").strip().rstrip("/")
    if not (base.startswith("https://") or base.startswith("http://")):
        raise PluginError("server_url must be an http or https origin.")
    return base + "/mcp"


class ParanoyClient:
    def __init__(self, server_url: str, secret_hex: str, *, clock=None, nonce=None, poster=None):
        self.server_url = str(server_url).strip().rstrip("/")
        self._url = mcp_url(self.server_url)
        self._signer = Signer(secret_hex, clock=clock)
        self._nonce = nonce or None
        self._poster = poster
        self._secret_hex = secret_hex

    def __repr__(self) -> str:
        return f"ParanoyClient(server_url={self.server_url!r})"

    @property
    def npub(self) -> str:
        return self._signer.npub

    @property
    def pubkey(self) -> str:
        return self._signer.pubkey

    def sign(self, kind: int, content: dict, tags: list | None = None) -> dict:
        return self._signer.sign_event(kind, content, tags=tags)

    def call(self, tool: str, args: dict | None = None) -> dict:
        event = self._signer.sign_request(tool, dict(args or {}), nonce=None if self._nonce is None else self._nonce())
        return self._rpc(tool, {"request": event})

    def publish(self, events: list[dict]) -> dict:
        return self._rpc("publish", {"events": events})

    def _rpc(self, tool: str, arguments: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        try:
            if self._poster is not None:
                body = self._poster(self._url, payload)
            else:
                response = httpx.post(
                    self._url,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json=payload,
                    timeout=60.0,
                )
                body = response.json()
        except PluginError:
            raise
        except Exception as exc:
            raise PluginError("Could not reach the Paranoy server.") from exc
        return self._unwrap(body)

    def _unwrap(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise PluginError("The Paranoy server returned an unreadable response.")
        if body.get("error"):
            message = body["error"].get("message") if isinstance(body["error"], dict) else "MCP error"
            raise PluginError(self._public_server_message(message))
        result = body.get("result") or {}
        if result.get("isError"):
            content = result.get("content") or []
            text = ""
            if content and isinstance(content, list) and isinstance(content[0], dict):
                text = str(content[0].get("text") or "")
            raise PluginError(self._public_server_message(text or "The Paranoy tool call failed."))
        structured = result.get("structuredContent")
        if structured is None:
            return {}
        if not isinstance(structured, dict):
            return {"result": structured}
        return structured

    def _public_server_message(self, message: str) -> str:
        text = redact(message, self._secret_hex)
        lowered = text.lower()
        if "sign the current terms first" in lowered:
            text += (
                " Update terms_version and terms_sha256 to the values published "
                "with the server docs. There is no machine-readable terms endpoint."
            )
        return text


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)

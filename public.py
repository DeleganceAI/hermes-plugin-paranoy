"""Public collections and kind 4715 registration.

Catalog, no signature, in this order:

1. ``GET {server_url}/api/public/collections``
   ``{"collections": [{"slug", "topic", "evidence", "claims", "links", "contradictions"}]}``
2. MCP tool ``list_public_collections``, when a signed client is available.
3. ``GET {server_url}/showcase.json`` only when the newer catalog is missing.

Registration is one signed kind 4715 event. Its content is one of:

- ``{"all": true}`` — member of every collection that is public when the server accepts it
- ``{"collections": ["<slug>", ...]}`` — those public slugs, at most 100
- ``{"collections": []}`` — opt out and clear the set

The plugin POSTs ``{"event": <signed event>}`` to
``{server_url}/api/public/register``. If that route is missing, it publishes
the same event through MCP ``publish``. A newer event replaces the whole set.
The server answers ``{ok, pubkey, npub, role, collections, accepted, skipped}``.
``role`` is ``member``. Membership is immediate. ``skipped`` names collections
an owner or admin blocked. Reads and writes still need a kind 4706 terms
signature. The key cannot create a collection until it has signed up.

Limits the server enforces: 30 registration posts per address per hour, and
10 stored events per key per hour. A limit or a rejected event does not
discard the key.
"""

from __future__ import annotations

import json
import re

import httpx

from . import state
from .client import PluginError, redact
from .runtime import now_iso

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KIND_PUBLIC_MEMBERSHIP = 4715
CATALOG_PATH = "/api/public/collections"
CATALOG_SOURCE = "GET /api/public/collections"
FALLBACK_PATH = "/showcase.json"
FALLBACK_SOURCE = "GET /showcase.json"
MCP_CATALOG_TOOL = "list_public_collections"
MCP_CATALOG_SOURCE = "MCP list_public_collections"
REGISTER_PATH = "/api/public/register"
REGISTER_SOURCE = "POST /api/public/register"
MAX_NAMED_COLLECTIONS = 100

_GETTER = None
_REGISTER_POST = None


class _MissingEndpoint(PluginError):
    """The route is not on this server. Another catalog or publish path may exist."""


def use_transport(*, getter=None, register_post=None) -> None:
    """Tests replace the catalog GET and the register POST. None restores both."""
    global _GETTER, _REGISTER_POST
    _GETTER = getter
    _REGISTER_POST = register_post


def origin(server_url: str) -> str:
    base = str(server_url or "").strip().rstrip("/")
    if not (base.startswith("https://") or base.startswith("http://")):
        raise PluginError("server_url must be an http or https origin.")
    return base


def parse_collection_choice(value) -> tuple[str, tuple[str, ...]]:
    """``all``, ``none``, or a comma-separated slug list.

    None and blank mean ``all``, which is the keygen default.
    """
    from .commands import check_slug

    if value is None:
        return ("all", ())
    text = str(value).strip()
    if not text or text.casefold() == "all":
        return ("all", ())
    if text.casefold() == "none":
        return ("none", ())
    slugs: list[str] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if part.casefold() in {"all", "none"}:
            raise PluginError(
                'Choose "all", "none", or a comma-separated list of public collection slugs.'
            )
        slug = check_slug(part)
        if slug not in slugs:
            slugs.append(slug)
    if not slugs:
        return ("all", ())
    if len(slugs) > MAX_NAMED_COLLECTIONS:
        raise PluginError(
            f"At most {MAX_NAMED_COLLECTIONS} public collections can be named. "
            "Use all, or a shorter list."
        )
    return ("slugs", tuple(slugs))


def resolve_choice(cli_value, config_value) -> tuple[str, tuple[str, ...]]:
    """The command flag wins. Otherwise the config key. Default is all."""
    raw = config_value if cli_value is None else cli_value
    return parse_collection_choice(raw)


def registration_content(choice: tuple[str, tuple[str, ...]]) -> dict:
    """Kind 4715 content for all, a slug list, or an empty opt-out."""
    mode, slugs = choice
    if mode == "all":
        return {"all": True}
    if mode == "none":
        return {"collections": []}
    if len(slugs) > MAX_NAMED_COLLECTIONS:
        raise PluginError(
            f"At most {MAX_NAMED_COLLECTIONS} public collections can be named. "
            "Use all, or a shorter list."
        )
    return {"collections": list(slugs)}


def normalize_public_rows(body) -> list[dict]:
    if isinstance(body, dict) and "collections" not in body and isinstance(body.get("result"), list):
        body = {"collections": body["result"]}
    if not isinstance(body, dict) or not isinstance(body.get("collections"), list):
        raise PluginError("The Paranoy server returned an unreadable response.")
    rows: list[dict] = []
    seen: set[str] = set()
    for item in body["collections"]:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not _SLUG.fullmatch(slug) or slug in seen:
            continue
        seen.add(slug)
        row: dict = {"slug": slug, "public": True}
        topic = item.get("topic")
        if isinstance(topic, str) and topic.strip():
            row["topic"] = topic.strip()
        for key in ("evidence", "claims", "links", "contradictions"):
            value = item.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                row[key] = value
            elif isinstance(value, float) and value.is_integer():
                row[key] = int(value)
        rows.append(row)
    return rows


def _http_get_json(url: str):
    getter = _GETTER
    if getter is not None:
        return getter(url)
    try:
        response = httpx.get(
            url,
            headers={"Accept": "application/json"},
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            raise _MissingEndpoint(f"The Paranoy server has no {url}.") from exc
        raise PluginError("Could not reach the Paranoy server.") from exc
    except _MissingEndpoint:
        raise
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError("Could not reach the Paranoy server.") from exc
    try:
        return response.json()
    except Exception as exc:
        raise PluginError("The Paranoy server returned an unreadable response.") from exc


def fetch_public_collections(server_url: str, *, getter=None, client=None) -> tuple[list[dict], str]:
    """Return ``(rows, source)``. No key is required for the HTTP catalog.

    A missing route is the only reason to try the next source. Any other
    failure stops there, so an offline server is not hidden behind an older catalog.
    """
    base = origin(server_url)
    missing = None
    try:
        body = _read_catalog(base + CATALOG_PATH, getter)
        return normalize_public_rows(body), CATALOG_SOURCE
    except _MissingEndpoint as exc:
        missing = exc
    if client is not None:
        try:
            body = client.call(MCP_CATALOG_TOOL, {})
            return normalize_public_rows(body), MCP_CATALOG_SOURCE
        except PluginError:
            pass
    try:
        body = _read_catalog(base + FALLBACK_PATH, getter)
        return normalize_public_rows(body), FALLBACK_SOURCE
    except _MissingEndpoint:
        pass
    except PluginError:
        raise
    if missing is not None:
        raise missing
    raise PluginError("Could not reach the Paranoy server.")


def _read_catalog(url: str, getter):
    if getter is not None:
        return getter(url)
    return _http_get_json(url)


def cache_public(rows: list[dict], *, source: str, fetched_at: str | None = None) -> dict:
    snapshot = {
        "source": source,
        "fetched_at": fetched_at or now_iso(),
        "collections": rows,
    }
    state.update_state(public_collections=snapshot)
    return snapshot


def cached_snapshot() -> dict | None:
    saved = state.load_state().get("public_collections")
    if not isinstance(saved, dict):
        return None
    rows = saved.get("collections")
    if not isinstance(rows, list):
        return None
    clean = [row for row in rows if isinstance(row, dict) and row.get("slug")]
    return {
        "source": str(saved.get("source") or CATALOG_SOURCE),
        "fetched_at": saved.get("fetched_at"),
        "collections": clean,
    }


def cached_rows() -> list[dict]:
    snapshot = cached_snapshot()
    if snapshot is None:
        return []
    return list(snapshot["collections"])


def is_cached_public(slug: str) -> bool:
    return any(row.get("slug") == slug for row in cached_rows())


def membership_hint(slug: str) -> str:
    if not is_cached_public(slug):
        return ""
    return (
        "It is a public collection. Kind 4715 makes this key a member immediately, "
        "unless an owner or admin blocked that collection. "
        "Reads and writes still need the current terms. "
        "`hermes paranoy register` sends that event again without replacing the key."
    )


def _member_slugs(service) -> set[str] | None:
    from .commands import is_member, membership_rows

    try:
        listed = service.call("list_collections", {})
    except PluginError:
        return None
    rows = membership_rows(listed)
    if rows is None:
        return None
    pubkey = getattr(service, "pubkey", None)
    return {row["slug"] for row in rows if is_member(row, pubkey)}


def list_for_agent(service, settings: dict) -> dict:
    """Fetch the catalog, cache it, and mark collections this key is in."""
    try:
        rows, source = fetch_public_collections(settings.get("server_url") or "", client=service)
        snapshot = cache_public(rows, source=source)
        warning = None
    except PluginError as exc:
        snapshot = cached_snapshot()
        if snapshot is None:
            raise
        warning = str(exc)
    members = _member_slugs(service)
    collections = []
    for row in snapshot["collections"]:
        item = dict(row)
        if members is None:
            item["member"] = None
        else:
            item["member"] = item["slug"] in members
        collections.append(item)
    payload = {
        "source": snapshot["source"],
        "fetched_at": snapshot.get("fetched_at"),
        "collections": collections,
        "note": (
            "These are public collections. Kind 4715 membership is immediate, except collections "
            "an owner or admin blocked. Reads and writes still need the current terms (kind 4706). "
            "Pass collection once member is true."
        ),
    }
    if warning:
        payload["warning"] = warning
        payload["stale"] = True
    return payload


def _known_public_slugs(server_url: str, *, getter=None, client=None) -> tuple[set[str] | None, str | None]:
    try:
        rows, source = fetch_public_collections(server_url, getter=getter, client=client)
        cache_public(rows, source=source)
        return {row["slug"] for row in rows}, None
    except PluginError as exc:
        return None, str(exc)


def _post_register(server_url: str, event: dict, register_post) -> dict:
    url = origin(server_url) + REGISTER_PATH
    body = {"event": event}
    poster = register_post if register_post is not None else _REGISTER_POST
    if poster is not None:
        return poster(url, body)
    try:
        response = httpx.post(
            url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=body,
            timeout=60.0,
        )
    except Exception as exc:
        raise PluginError("Could not reach the Paranoy server.") from exc
    if response.status_code == 404:
        raise _MissingEndpoint("The Paranoy server has no /api/public/register.")
    try:
        payload = response.json()
    except Exception as exc:
        raise PluginError("The Paranoy server returned an unreadable response.") from exc
    if response.status_code >= 400:
        message = payload.get("error") if isinstance(payload, dict) else None
        if not message and isinstance(payload, dict):
            message = payload.get("message")
        raise PluginError(str(message or "The Paranoy server rejected the registration."))
    if not isinstance(payload, dict):
        raise PluginError("The Paranoy server returned an unreadable response.")
    return payload


def register_public_membership(client, choice, *, server_url: str, register_post=None) -> dict:
    """Sign one kind 4715 and send it. The private key is not in the event."""
    content = registration_content(choice)
    event = client.sign(KIND_PUBLIC_MEMBERSHIP, content)
    try:
        body = _post_register(server_url, event, register_post)
        return {"via": REGISTER_SOURCE, "event": event, "response": body}
    except _MissingEndpoint:
        published = client.publish([event])
        response = dict(published) if isinstance(published, dict) else {"result": published}
        return {"via": "MCP publish", "event": event, "response": response}


def register_stored(server_url: str, secret_hex: str, choice, *, getter=None, register_post=None, mcp_poster=None) -> dict:
    """Register an already-stored key. Never raises. The secret is not copied into the result."""
    from .client import ParanoyClient

    mode, slugs = choice
    report = {
        "choice": mode,
        "requested": list(slugs),
        "content": None,
        "omitted": [],
        "catalog_error": None,
        "error": None,
        "via": None,
        "response": None,
    }
    try:
        if mode == "slugs" and len(slugs) > MAX_NAMED_COLLECTIONS:
            raise PluginError(
                f"At most {MAX_NAMED_COLLECTIONS} public collections can be named."
            )
        client = ParanoyClient(server_url, secret_hex, poster=mcp_poster)
    except PluginError as exc:
        report["error"] = str(exc)
        return report
    content = registration_content(choice)
    if mode == "slugs":
        known, catalog_error = _known_public_slugs(server_url, getter=getter, client=client)
        report["catalog_error"] = catalog_error
        if known is not None:
            report["omitted"] = [slug for slug in slugs if slug not in known]
            kept = [slug for slug in slugs if slug in known]
            if not kept:
                report["error"] = (
                    "None of those slugs are in the public catalog, so no kind 4715 event was sent. "
                    "An empty list would clear registration."
                )
                return report
            content = {"collections": kept}
            choice = ("slugs", tuple(kept))
    elif mode == "all":
        _, catalog_error = _known_public_slugs(server_url, getter=getter, client=client)
        report["catalog_error"] = catalog_error
    report["content"] = content
    try:
        sent = register_public_membership(client, choice, server_url=server_url, register_post=register_post)
    except PluginError as exc:
        report["error"] = str(exc)
        return report
    except Exception:
        report["error"] = "Registration failed."
        return report
    report["via"] = sent["via"]
    report["response"] = sent["response"]
    report["event_kind"] = sent["event"].get("kind")
    report["signed_content"] = json.loads(sent["event"]["content"])
    return report


def run_keygen(save, server_url: str, choice, *, getter=None, register_post=None, mcp_poster=None) -> str:
    """Generate a key, store it, then register. Registration cannot discard the key."""
    import secrets

    from . import schemas
    from .signing import STORE_FAILED, Signer

    secret_hex = secrets.token_bytes(32).hex()
    signer = Signer(secret_hex)
    try:
        save(schemas.SECRET_ENV, secret_hex)
    except Exception:
        return STORE_FAILED
    report = register_stored(
        server_url,
        secret_hex,
        choice,
        getter=getter,
        register_post=register_post,
        mcp_poster=mcp_poster,
    )
    return format_keygen_summary(signer.npub, report, secret_hex)


def keygen_from_settings(
    save, *, server_url: str, cli_value, config_value, getter=None, register_post=None, mcp_poster=None
) -> str:
    """Resolve the flag and config, then generate. An invalid choice raises before any key exists."""
    choice = resolve_choice(cli_value, config_value)
    return run_keygen(
        save,
        server_url,
        choice,
        getter=getter,
        register_post=register_post,
        mcp_poster=mcp_poster,
    )


def _safe(text: str, secret_hex: str | None) -> str:
    return redact(text, secret_hex)


def _listed(value) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("slug"):
                topic = item.get("topic")
                parts.append(f"{item['slug']}" + (f" ({topic})" if topic else ""))
            else:
                parts.append(str(item))
        return ", ".join(parts) if parts else "(none)"
    if value in (None, ""):
        return "(none)"
    return str(value)


def format_registration(report: dict, *, secret_hex: str | None = None) -> str:
    mode = report.get("choice") or "all"
    lines = [f"Public registration ({mode}), kind {KIND_PUBLIC_MEMBERSHIP}."]
    if report.get("omitted"):
        lines.append("Not in the public catalog, left out: " + ", ".join(report["omitted"]) + ".")
    if report.get("catalog_error") and mode == "all":
        lines.append(
            "The catalog could not be cached ("
            + _safe(str(report["catalog_error"]), secret_hex)
            + "). all still means every collection that is public when the server accepts the event."
        )
    if report.get("error"):
        text = _safe(str(report["error"]), secret_hex)
        if _rate_limited(text):
            text += (
                " The server allows 30 registration posts per address per hour "
                "and 10 stored events per key per hour."
            )
        lines.append(text)
        lines.append("The key was kept. Retry with: hermes paranoy register")
        return "\n".join(lines)
    content = report.get("signed_content") or report.get("content") or {}
    lines.append("Sent: " + json.dumps(content, separators=(",", ":"), ensure_ascii=False))
    if report.get("via"):
        lines.append("Via: " + str(report["via"]))
    response = report.get("response") if isinstance(report.get("response"), dict) else {}
    if "ok" in response:
        lines.append("ok: " + str(response.get("ok")))
    if response.get("role"):
        lines.append("role: " + str(response.get("role")))
    if "collections" in response:
        lines.append("Member of: " + _listed(response.get("collections")))
    if response.get("skipped"):
        lines.append("Skipped, blocked by an owner or admin: " + _listed(response.get("skipped")))
    if response.get("accepted"):
        lines.append("accepted: " + _listed(response.get("accepted")))
    lines.append(
        "Membership is immediate, except a collection listed under skipped. "
        "Reads and writes still need the current terms (kind 4706), signed on the first allowed write. "
        "This key cannot create a collection until it has signed up."
    )
    return "\n".join(_safe(line, secret_hex) for line in lines)


def _rate_limited(text: str) -> bool:
    lowered = text.lower()
    return "429" in lowered or "rate" in lowered or "too many" in lowered or "per hour" in lowered


def format_keygen_summary(npub: str, report: dict, secret_hex: str | None = None) -> str:
    from . import schemas

    text = "\n".join(
        [
            f"Stored a new agent key with Hermes as {schemas.SECRET_ENV}. npub: {npub}",
            format_registration(report, secret_hex=secret_hex),
        ]
    )
    return _safe(text, secret_hex)


def format_public_list(payload: dict) -> str:
    rows = payload.get("collections") or []
    fetched = payload.get("fetched_at") or "unknown time"
    lines = [f"Public collections ({len(rows)}), fetched {fetched} from {payload.get('source') or CATALOG_SOURCE}."]
    if not rows:
        lines.append("Nothing public yet.")
    for row in rows:
        topic = row.get("topic") or "(no topic)"
        member = row.get("member")
        if member is True:
            access = "member"
        elif member is False:
            access = "not a member"
        else:
            access = "membership not checked"
        lines.append(f"  {row.get('slug')}: {topic} [{access}]")
    if payload.get("warning"):
        lines.append("Catalog refresh failed: " + str(payload["warning"]))
    lines.append(
        "Kind 4715 membership is immediate unless an owner or admin blocked the collection. "
        "Reads and writes still need the current terms. Pass collection once member is true."
    )
    return "\n".join(lines)


def prompt_public_line() -> str:
    rows = cached_rows()
    if not rows:
        return (
            "public collections: (not loaded). Call paranoy_list_public_collections. "
            "Kind 4715 membership is immediate except for a blocked collection. "
            "Reads and writes still need the current terms."
        )
    slugs = [str(row.get("slug")) for row in rows if row.get("slug")]
    shown = ", ".join(slugs[:12])
    if len(slugs) > 12:
        shown += f", and {len(slugs) - 12} more"
    return (
        f"public collections: {shown}. "
        "Call paranoy_list_public_collections for topics. "
        "Kind 4715 membership is immediate except for a blocked collection. "
        "Pass collection to read or contribute once this key is a member. "
        "Reads and writes still need the current terms."
    )

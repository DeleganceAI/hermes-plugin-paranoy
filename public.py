"""Public collections: the catalog the server already publishes, and key registration.

Discovery uses the unsigned catalog the Communities page already reads:

    GET {server_url}/showcase.json
    {"collections": [{"slug", "topic", "created_at", "evidence", "claims",
                      "links", "contradictions", "last_at", "contributors", ...}]}

``shape`` and ``daily`` are display data and are not cached. Every row in that
catalog is a public collection. Its records and member list stay private until
this key is a member. Reading and contributing use the existing collection
tools, and only succeed once membership is real.

Registration is isolated in ``register_key``. The live MCP tool list has no
self-join tool, and kind 4705 is signed by an owner or admin, so this module
does not publish a members event. ``register_key`` sends one signed kind 4707
request whose tool name is ``register_key``. The server has to provide that
tool. It should record the same join request as ``POST /api/c/<slug>/join``
on the Communities page: the owner or an admin accepts it by signing a new
kind 4705 list. A repeat call for a key that is already a member, or that
already has a request waiting, must succeed.

Expected ``structuredContent``:

    {"collection": "<slug>", "status": "registered"|"already"|"pending", "detail": "<optional>"}

``registered`` means the key is a member now. ``already`` means it was already
a member or already requested. ``pending`` means the request is waiting for
an owner or admin. The plugin also classifies error text, so a server that
rejects with "approval required" or "already a member" is reported and does
not abort the rest of keygen.
"""

from __future__ import annotations

import re

import httpx

from . import state
from .client import PluginError, redact
from .runtime import now_iso

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PUBKEY = re.compile(r"^[0-9a-fA-F]{64}$")
REGISTER_TOOL = "register_key"
CATALOG_PATH = "/showcase.json"
CATALOG_SOURCE = "GET /showcase.json"

_ALREADY = re.compile(
    r"already (?:a )?member|already requested|already pending|already registered|duplicate",
    re.IGNORECASE,
)
_PENDING = re.compile(
    r"requires approval|approval required|pending|awaiting|owner must|needs approval",
    re.IGNORECASE,
)
_UNAVAILABLE = re.compile(
    r"unknown tool|tool not found|no such tool|not implemented|method not found|unknown mcp",
    re.IGNORECASE,
)
_STATUSES = frozenset({"registered", "already", "pending", "rejected", "offline", "unavailable", "skipped"})
_SUCCESS_ALIASES = {
    "ok": "registered",
    "member": "registered",
    "joined": "registered",
    "created": "registered",
    "duplicate": "already",
    "exists": "already",
    "approval": "pending",
    "approval_required": "pending",
    "requested": "pending",
    "waiting": "pending",
}

_GETTER = None
_POSTER = None


def use_transport(*, getter=None, poster=None) -> None:
    """Tests replace the catalog GET and the MCP POST. None restores both."""
    global _GETTER, _POSTER
    _GETTER = getter
    _POSTER = poster


def showcase_url(server_url: str) -> str:
    base = str(server_url or "").strip().rstrip("/")
    if not (base.startswith("https://") or base.startswith("http://")):
        raise PluginError("server_url must be an http or https origin.")
    return base + CATALOG_PATH


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
    return ("slugs", tuple(slugs))


def resolve_choice(cli_value, config_value) -> tuple[str, tuple[str, ...]]:
    """The command flag wins. Otherwise the config key. Default is all."""
    raw = config_value if cli_value is None else cli_value
    return parse_collection_choice(raw)


def normalize_public_rows(body) -> list[dict]:
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
        for key in ("created_at", "last_at"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                row[key] = value.strip()
        for key in ("evidence", "claims", "links", "contradictions", "contributors"):
            value = item.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                row[key] = value
            elif isinstance(value, float) and value.is_integer():
                row[key] = int(value)
        rows.append(row)
    return rows


def _get_json(url: str):
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
            raise PluginError(
                "The Paranoy server has no public collection catalog at /showcase.json."
            ) from exc
        raise PluginError("Could not reach the Paranoy server.") from exc
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError("Could not reach the Paranoy server.") from exc
    try:
        return response.json()
    except Exception as exc:
        raise PluginError("The Paranoy server returned an unreadable response.") from exc


def fetch_public_collections(server_url: str, *, getter=None) -> list[dict]:
    """GET the public catalog. No signature and no key."""
    url = showcase_url(server_url)
    if getter is not None:
        body = getter(url)
    else:
        body = _get_json(url)
    return normalize_public_rows(body)


def cache_public(rows: list[dict], *, fetched_at: str | None = None) -> dict:
    snapshot = {
        "source": CATALOG_SOURCE,
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
        "It is a public collection. Its records stay private until an owner or admin "
        "accepts this key. Keygen registers with public collections by default, and "
        "`hermes paranoy register` retries that without replacing the key."
    )


def _check_pubkey(pubkey: str) -> str:
    text = str(pubkey or "").strip().lower()
    if not _PUBKEY.fullmatch(text):
        raise PluginError("register_key needs the 64-character hex public key.")
    return text


def _detail(result: dict) -> str:
    for key in ("detail", "message"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def classify_success(collection_id: str, result) -> dict:
    """A non-error MCP result. Unknown shapes still count as registered."""
    body = result if isinstance(result, dict) else {}
    raw_status = str(body.get("status") or "").strip().lower()
    status = _SUCCESS_ALIASES.get(raw_status, raw_status)
    if status not in _STATUSES:
        if body.get("pending") or body.get("approval_required"):
            status = "pending"
        elif body.get("already"):
            status = "already"
        else:
            status = "registered"
    row = {"collection": collection_id, "status": status}
    detail = _detail(body)
    if detail:
        row["detail"] = detail
    return row


def classify_error(collection_id: str, exc: PluginError) -> dict:
    text = str(exc)
    lowered = text.lower()
    if "could not reach" in lowered or "no public collection catalog" in lowered:
        status = "offline"
    elif _UNAVAILABLE.search(text):
        status = "unavailable"
    elif _ALREADY.search(text):
        status = "already"
    elif _PENDING.search(text):
        status = "pending"
    else:
        status = "rejected"
    return {"collection": collection_id, "status": status, "detail": text}


def register_key(collection_id: str, pubkey: str, *, client) -> dict:
    """Register one public key with one public collection.

    ``collection_id`` is the collection slug. ``pubkey`` is the hex public
    key and must be the key that signs the request. The private key is not
    an argument and is not sent.

    Server contract: MCP ``tools/call`` name ``register_key`` at
    ``{server_url}/mcp``, with a signed kind 4707 request whose content is
    ``{"tool": "register_key", "args": {"collection": "<slug>", "pubkey": "<hex>"}, "nonce": "..."}``.
    See the module docstring for the result this function expects.
    """
    from .commands import check_slug

    slug = check_slug(collection_id)
    pubkey = _check_pubkey(pubkey)
    if pubkey != str(getattr(client, "pubkey", "") or "").lower():
        raise PluginError("register_key only registers the key that signs the request.")
    result = client.call(REGISTER_TOOL, {"collection": slug, "pubkey": pubkey})
    return classify_success(slug, result)


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
        rows = fetch_public_collections(settings.get("server_url") or "")
        snapshot = cache_public(rows)
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
            "These are public collections. Records stay private until this key is a member. "
            "Pass collection to read, and to write when the mode and role allow it, only where "
            "member is true."
        ),
    }
    if warning:
        payload["warning"] = warning
        payload["stale"] = True
    return payload


def register_selection(client, choice: tuple[str, tuple[str, ...]], rows: list[dict]) -> list[dict]:
    """Register with each selected public collection. One failure does not stop the next."""
    mode, slugs = choice
    if mode == "none":
        return []
    by_slug = {row["slug"]: row for row in rows if row.get("slug")}
    targets = list(by_slug) if mode == "all" else list(slugs)
    results: list[dict] = []
    offline = False
    for slug in targets:
        if slug not in by_slug:
            results.append(
                {
                    "collection": slug,
                    "status": "skipped",
                    "detail": "Not in the public collection catalog.",
                }
            )
            continue
        if offline:
            results.append(
                {
                    "collection": slug,
                    "status": "offline",
                    "detail": "Could not reach the Paranoy server.",
                }
            )
            continue
        try:
            results.append(register_key(slug, client.pubkey, client=client))
        except PluginError as exc:
            row = classify_error(slug, exc)
            results.append(row)
            if row["status"] == "offline":
                offline = True
        except Exception:
            results.append({"collection": slug, "status": "rejected", "detail": "Registration failed."})
    return results


def register_stored(server_url: str, secret_hex: str, choice, *, poster=None, getter=None) -> dict:
    """Register an already-stored key. Never raises. The secret is not copied into the result."""
    from .client import ParanoyClient

    mode, slugs = choice
    report = {
        "choice": mode,
        "requested": list(slugs),
        "discovered": None,
        "discovery_error": None,
        "results": [],
    }
    if mode == "none":
        return report
    try:
        rows = fetch_public_collections(server_url, getter=getter)
        cache_public(rows)
    except PluginError as exc:
        report["discovery_error"] = str(exc)
        return report
    except Exception:
        report["discovery_error"] = "Could not reach the Paranoy server."
        return report
    report["discovered"] = len(rows)
    try:
        client = ParanoyClient(server_url, secret_hex, poster=poster if poster is not None else _POSTER)
    except PluginError as exc:
        report["discovery_error"] = str(exc)
        return report
    report["results"] = register_selection(client, choice, rows)
    return report


def run_keygen(save, server_url: str, choice, *, poster=None, getter=None) -> str:
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
        poster=poster,
        getter=getter,
    )
    return format_keygen_summary(signer.npub, report, secret_hex)


def keygen_from_settings(save, *, server_url: str, cli_value, config_value, poster=None, getter=None) -> str:
    """Resolve the flag and config, then generate. An invalid choice raises before any key exists."""
    choice = resolve_choice(cli_value, config_value)
    return run_keygen(save, server_url, choice, poster=poster, getter=getter)


def _safe(text: str, secret_hex: str | None) -> str:
    return redact(text, secret_hex)


def format_registration(report: dict, *, secret_hex: str | None = None) -> str:
    mode = report.get("choice") or "all"
    if mode == "none":
        return "Public registration: skipped (none)."
    lines: list[str] = []
    if report.get("discovery_error"):
        lines.append(
            "Public registration: could not list public collections ("
            + _safe(str(report["discovery_error"]), secret_hex)
            + "). The key was kept. Retry with: hermes paranoy register"
        )
        return "\n".join(lines)
    results = report.get("results") or []
    if mode == "all":
        lines.append(f"Public registration (all, {len(results)} collections):")
    else:
        requested = ", ".join(report.get("requested") or []) or "(none)"
        lines.append(f"Public registration ({requested}):")
    if not results:
        lines.append("  No public collections are listed.")
        return "\n".join(lines)
    counts = {"registered": 0, "already": 0, "pending": 0, "failed": 0}
    for row in results:
        status = str(row.get("status") or "rejected")
        if status in {"registered", "already", "pending"}:
            counts[status] += 1
        else:
            counts["failed"] += 1
        detail = _safe(str(row.get("detail") or ""), secret_hex)
        suffix = f" — {detail}" if detail else ""
        lines.append(f"  {row.get('collection')}: {status}{suffix}")
    lines.append(
        f"{counts['registered']} registered, {counts['already']} already registered, "
        f"{counts['pending']} waiting for approval, {counts['failed']} not registered."
    )
    if counts["failed"]:
        lines.append("The key was kept. Retry with: hermes paranoy register")
    return "\n".join(lines)


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
    lines = [f"Public collections ({len(rows)}), fetched {fetched}."]
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
        "Records stay private until this key is a member. "
        "Pass collection on a read or write once it is."
    )
    return "\n".join(lines)


def prompt_public_line() -> str:
    rows = cached_rows()
    if not rows:
        return (
            "public collections: (not loaded). Call paranoy_list_public_collections. "
            "Pass collection to read or contribute once this key is a member."
        )
    slugs = [str(row.get("slug")) for row in rows if row.get("slug")]
    shown = ", ".join(slugs[:12])
    if len(slugs) > 12:
        shown += f", and {len(slugs) - 12} more"
    return (
        f"public collections: {shown}. "
        "Call paranoy_list_public_collections for topics. "
        "Pass collection to read or contribute once this key is a member."
    )

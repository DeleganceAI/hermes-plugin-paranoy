"""Rank ledger claims by evidence weight for one question.

The rank is supporting links minus rebutting links. Qualifying links are
reported and do not change the net. Nothing here is a verdict. A claim the
search does not return is not added.
"""

from __future__ import annotations

import re

from .client import PluginError
from .commands import membership_rows

DEMO_QUESTION = "what is the worst seed oil and why"
RANKING_RULE = (
    "Ranked by net links: supporting count minus rebutting count. "
    "Qualifying links are reported and do not change the net. "
    "Ties go to the claim with more supporting links, then more links in total. "
    "This is not a verdict and does not judge plausibility or tone."
)
_STOP = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are",
        "was", "were", "what", "why", "how", "which", "who", "when", "where",
        "does", "do", "did", "worst", "best", "most", "least", "than", "with",
        "from", "that", "this", "into", "about", "why",
    }
)
_WORD = re.compile(r"[a-z0-9]+")


def answer(service, settings: dict, args: dict) -> dict:
    question = " ".join(str((args or {}).get("question") or "").split())
    if not question:
        raise PluginError("ask needs a question.")
    rows = _rows(service)
    requested = str((args or {}).get("collection") or "").strip()
    chosen = _select(rows, question, requested)
    items, method = _search(service, chosen["slug"], question)
    index = _evidence_index(service, chosen["slug"]) if items else {}
    options = [_option(service, chosen["slug"], item, index) for item in items]
    options.sort(key=_sort_key)
    for index, option in enumerate(options, start=1):
        option["rank"] = index
    url = _collection_url(settings.get("server_url"), chosen["slug"])
    payload = {
        "question": question,
        "collection": chosen["slug"],
        "topic": chosen.get("topic"),
        "public": chosen.get("public"),
        "url": url,
        "search": method,
        "ranking": RANKING_RULE,
        "options": options,
        "answered": bool(options),
    }
    payload["card"] = format_card(payload)
    return payload


def format_card(payload: dict) -> str:
    slug = payload["collection"]
    lines = [
        f"Evidence rank in {slug}",
        f"Question: {payload['question']}",
        RANKING_RULE,
    ]
    if payload.get("topic"):
        lines.append(f"Topic: {payload['topic']}")
    if payload.get("public") is True:
        lines.append("Collection visibility: public")
    elif payload.get("public") is False:
        lines.append("Collection visibility: private")
    lines.append(f"Search: {payload.get('search')}. Relevance is the search result, not a judgment.")
    options = payload.get("options") or []
    if not options:
        lines.append(
            f"{slug} has no claim that answers this question. Nothing was added."
        )
    for option in options:
        lines.append(
            f"{option['rank']}. {option['text']}"
        )
        lines.append(
            f"   support {option['supports']} · rebut {option['rebuts']} · "
            f"qualify {option['qualifies']} · net {option['net']}"
        )
        _source_lines(lines, "support", option["support_sources"])
        _source_lines(lines, "rebut", option["rebut_sources"])
        _source_lines(lines, "qualify", option["qualify_sources"])
        lines.append(f"   would be rebutted by: {option['would_be_rebutted_by']}")
        lines.append(f"   {option['thinness']}")
    lines.append(f"Collection: {payload['url']}")
    if options and options[0].get("id"):
        lines.append(f"Top claim id: {options[0]['id']}")
    lines.append("Counts are retrieval aids, not a verdict.")
    return "\n".join(lines)


def _source_lines(lines: list[str], label: str, sources: list[dict]) -> None:
    if not sources:
        lines.append(f"   {label}: none on the ledger")
        return
    for source in sources:
        url = source.get("url")
        name = source.get("title") or source.get("id")
        if url:
            lines.append(f"   {label}: {name} ({url})")
        else:
            lines.append(f"   {label}: {name} (no url on the ledger)")


def _rows(service) -> list[dict]:
    listed = service.call("list_collections", {})
    rows = membership_rows(listed)
    if rows is None:
        raise PluginError("Collections could not be listed, so nothing was answered.")
    return rows


def _select(rows: list[dict], question: str, requested: str) -> dict:
    if requested:
        row = next((item for item in rows if item.get("slug") == requested), None)
        if row is None:
            slugs = ", ".join(sorted(item["slug"] for item in rows)) or "(none)"
            raise PluginError(
                f"{requested} is not in the collections this key can list, so nothing was answered. "
                f"Collections: {slugs}."
            )
        return row
    question_tokens = _tokens(question)
    scored = []
    for row in rows:
        slug = str(row.get("slug") or "")
        topic = str(row.get("topic") or "")
        hay = _tokens(slug.replace("-", " ") + " " + topic)
        score = len(question_tokens & hay)
        if slug and slug in question.lower():
            score += 100
        scored.append((score, 1 if row.get("public") is True else 0, slug, row))
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    if not scored or scored[0][0] <= 0:
        slugs = ", ".join(sorted(item["slug"] for item in rows)) or "(none)"
        raise PluginError(
            "No listed collection matches that question, so nothing was answered. "
            f"Collections: {slugs}."
        )
    return scored[0][3]


def _search(service, collection: str, question: str) -> tuple[list[dict], str]:
    last_error = None
    for method in ("vector", "keyword"):
        try:
            found = service.call(
                "search_claims",
                {"collection": collection, "text": question, "method": method, "limit": 8},
            )
        except PluginError as exc:
            last_error = exc
            continue
        items = found.get("items") if isinstance(found, dict) else None
        clean = [item for item in items if isinstance(item, dict) and str(item.get("text") or "").strip()] if isinstance(items, list) else []
        if clean or method == "keyword":
            return clean, method
    if last_error is not None:
        raise last_error
    return [], "keyword"


def _option(service, collection: str, item: dict, index: dict) -> dict:
    links = item.get("links")
    if not isinstance(links, list):
        links = _links_for(service, collection, str(item.get("id") or ""))
    grouped = {"SUPPORTS": [], "REBUTS": [], "QUALIFIES": []}
    for link in links:
        if not isinstance(link, dict):
            continue
        relation = str(link.get("relation") or "").upper()
        if relation in grouped:
            grouped[relation].append(link)
    supports = len(grouped["SUPPORTS"])
    rebuts = len(grouped["REBUTS"])
    qualifies = len(grouped["QUALIFIES"])
    return {
        "id": item.get("id"),
        "text": str(item.get("text") or "").strip(),
        "supports": supports,
        "rebuts": rebuts,
        "qualifies": qualifies,
        "net": supports - rebuts,
        "support_sources": _sources(service, index, grouped["SUPPORTS"]),
        "rebut_sources": _sources(service, index, grouped["REBUTS"]),
        "qualify_sources": _sources(service, index, grouped["QUALIFIES"]),
        "would_be_rebutted_by": _rebuttal(item),
        "thinness": _thinness(supports, rebuts, qualifies),
    }


def _links_for(service, collection: str, claim_id: str) -> list[dict]:
    if not claim_id:
        return []
    found = service.call("get_links", {"collection": collection, "to_id": claim_id, "limit": 1000})
    rows = found.get("result") if isinstance(found, dict) else None
    return rows if isinstance(rows, list) else []


def _evidence_index(service, collection: str) -> dict:
    found = service.call("list_evidence", {"collection": collection, "limit": 500, "offset": 0})
    rows = found.get("result") if isinstance(found, dict) else None
    if not isinstance(rows, list):
        return {}
    return {row.get("id"): row for row in rows if isinstance(row, dict) and row.get("id")}


def _sources(service, index: dict, links: list[dict]) -> list[dict]:
    seen = []
    sources = []
    for link in links:
        evidence_id = str(link.get("from") or "").strip()
        if not evidence_id or evidence_id in seen:
            continue
        seen.append(evidence_id)
        row = index.get(evidence_id) or _load_event(service, evidence_id)
        sources.append(
            {
                "id": evidence_id,
                "title": row.get("title") or row.get("source") or evidence_id,
                "url": row.get("url") or row.get("archive_url") or None,
            }
        )
        if len(sources) == 2:
            break
    return sources


def _load_event(service, evidence_id: str) -> dict:
    try:
        loaded = service.call("get_event", {"id": evidence_id})
    except PluginError:
        return {"id": evidence_id}
    result = loaded.get("result") if isinstance(loaded, dict) else None
    content = result.get("content") if isinstance(result, dict) and isinstance(result.get("content"), dict) else {}
    return {
        "id": evidence_id,
        "title": content.get("title") or content.get("source"),
        "url": content.get("url") or content.get("archive_url"),
        "source": content.get("source"),
    }


def _rebuttal(item: dict) -> str:
    for key in ("would_be_rebutted_by",):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content")
    if isinstance(content, dict):
        value = content.get("would_be_rebutted_by")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Not on this claim. The ledger does not store that line."


def _thinness(supports: int, rebuts: int, qualifies: int) -> str:
    total = supports + rebuts + qualifies
    if total == 0:
        return "Evidence is missing: no links."
    notes = []
    if supports and not rebuts:
        notes.append("one-sided toward support")
    elif rebuts and not supports:
        notes.append("one-sided toward rebuttal")
    if total < 3:
        notes.append(f"thin ({total} link{'s' if total != 1 else ''})")
    if qualifies and not (supports and rebuts):
        notes.append("qualifying links do not decide the rank")
    if not notes:
        return "Supporting and rebutting links are both present."
    return "Evidence note: " + "; ".join(notes) + "."


def _sort_key(option: dict):
    return (-option["net"], -option["supports"], -(option["supports"] + option["rebuts"] + option["qualifies"]), option["text"])


def _tokens(text: str) -> set[str]:
    found = set()
    for word in _WORD.findall(str(text or "").lower()):
        if word in _STOP or len(word) < 3:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        if word not in _STOP and len(word) >= 3:
            found.add(word)
    return found


def _collection_url(server_url: str | None, slug: str) -> str:
    base = str(server_url or "https://paranoy.org").strip().rstrip("/") or "https://paranoy.org"
    return f"{base}/?c={slug}"

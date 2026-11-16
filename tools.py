"""The agent's tools. All of them are available whenever a key and a collection are set.

The agent never signs anything and never sees the key: each handler signs
in-process through the client. Writes need no approval. Membership of the
collection is the permission.
"""

from __future__ import annotations

import json
import logging
import re

from . import ground, ledger, state
from .client import PluginError, redact

logger = logging.getLogger(__name__)

_NSEC = re.compile(r"nsec1[02-9ac-hj-np-z]+")
_ID = {"type": "string", "description": "A 64-character record id."}


def _text(args: dict, key: str) -> str:
    return str(args.get(key) or "").strip()


def search(service, settings, args, ctx):
    query = _text(args, "query")
    if not query:
        raise PluginError("query is required.")
    method = _text(args, "method") or "vector"
    if method not in ("vector", "keyword"):
        raise PluginError("method is vector or keyword.")
    return {"items": ledger.search(service, settings["collection"], query, method, args.get("limit"))}


def read(service, settings, args, ctx):
    request = {"id": _text(args, "id")}
    if args.get("offset"):
        request["offset"] = int(args["offset"])
    return service.call("get_event", request)


def ingest(service, settings, args, ctx):
    llm = getattr(ctx, "llm", None)
    evidence_id, url, text = _text(args, "evidence_id"), _text(args, "url"), _text(args, "text")
    if evidence_id:
        return ledger.ingest(service, llm, settings, text=ledger.evidence_text(service, evidence_id), evidence_id=evidence_id)
    meta = {key: _text(args, key) for key in ("source", "title", "url")}
    if not text:
        if not url:
            raise PluginError("Pass url, or text with source, or evidence_id.")
        fetched = service.call("fetch_url", {"url": url})
        text = str(fetched.get("text") or "")
        meta["title"] = meta["title"] or str(fetched.get("title") or "")
    return ledger.ingest(service, llm, settings, text=text, meta=meta)


def store_evidence(service, settings, args, ctx):
    text = _text(args, "text")
    if not text:
        raise PluginError("text is required.")
    meta = {key: _text(args, key) for key in ("source", "title", "url", "retrieved_via")}
    if meta["retrieved_via"].lower() == "decision":
        meta["retrieved_via"] = "decision"
    event = service.sign(4701, ledger.evidence_content(settings["collection"], text, meta))
    ledger.publish(service, settings, [event])
    return {"evidence_id": event["id"]}


def store_claim(service, settings, args, ctx):
    collection = settings["collection"]
    text, claim_id = _text(args, "text"), _text(args, "claim_id")
    link = args.get("link") if isinstance(args.get("link"), dict) else None
    if bool(text) == bool(claim_id):
        raise PluginError("Pass text for a new claim, or claim_id to link evidence to an existing one.")
    if claim_id and not link:
        raise PluginError("claim_id needs a link. Without one there is nothing to store.")
    events = []
    reused = False
    if text:
        existing = next(
            (item for item in ledger.search(service, collection, text, "keyword", 20)
             if str(item.get("text") or "").strip() == text),
            None,
        )
        if existing:
            claim_id, reused = existing["id"], True
        else:
            events.append(service.sign(4702, {"collection": collection, "text": text}))
            claim_id = events[0]["id"]
    if link:
        relation = _text(link, "relation").upper()
        if relation not in ledger.RELATIONS:
            raise PluginError("link.relation is SUPPORTS, REBUTS or QUALIFIES.")
        if not _text(link, "evidence_id") or not _text(link, "reason"):
            raise PluginError("link needs evidence_id and reason.")
        content = {"collection": collection, "from": _text(link, "evidence_id"), "to": claim_id,
                   "relation": relation, "reason": _text(link, "reason")}
        if _text(link, "quote"):
            content["quote"] = _text(link, "quote")
        events.append(service.sign(4703, content))
    ledger.publish(service, settings, events)
    result = {"claim_id": claim_id, "already_existed": reused}
    if link:
        result["link_id"] = events[-1]["id"]
    return result


def check_proof(service, settings, args, ctx):
    if not isinstance(args.get("proof"), dict):
        raise PluginError("proof is an object with question and steps.")
    return service.call("check_proof", {"collection": settings["collection"], "proof": args["proof"]})


SPECS = [
    (search, "paranoy_search",
     ("Find claims in the ledger by meaning (method 'vector', the default) or by words (method 'keyword': every word "
     "must appear). Each result carries the links that target it, with their evidence ids, reasons and any quotes, "
     "and the ids of contradictions it belongs to. Order is a retrieval aid, not a judgement. Search as often as you "
     "need, with your own wording, and choose limit yourself (up to 200)."),
     {"query": {"type": "string"}, "method": {"type": "string", "enum": ["vector", "keyword"]},
      "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
     ["query"]),
    (read, "paranoy_read",
     ("Read one record by id. Evidence text comes back a window at a time with 'chars', the total: raise offset to "
     "read on."),
     {"id": _ID, "offset": {"type": "integer", "minimum": 0}},
     ["id"]),
    (ingest, "paranoy_ingest",
     ("Store a document as evidence and extract its claims in one step. Pass url (the server fetches it), or text "
     "with source, or evidence_id for a document already stored. Returns the evidence id, the claims stored, the "
     "existing claims the document restates (already linked as SUPPORTS), and related_claims: existing claims its "
     "searches turned up. You may link the evidence to a related claim it supports, rebuts or qualifies with "
     "paranoy_store_claim. That is optional. Read both before choosing the relation: closeness in a search does "
     "not tell you whether they agree."),
     {"url": {"type": "string"}, "text": {"type": "string"}, "source": {"type": "string"},
      "title": {"type": "string"}, "evidence_id": _ID},
     []),
    (store_evidence, "paranoy_store_evidence",
     ("Store one document as evidence, whole and verbatim, and nothing else. Use paranoy_ingest when you also want "
     "its claims. For a decision record, set retrieved_via to 'decision'."),
     {"text": {"type": "string"}, "source": {"type": "string", "description": "Who said or published it."},
      "title": {"type": "string"}, "url": {"type": "string"}, "retrieved_via": {"type": "string"}},
     ["text", "source"]),
    (store_claim, "paranoy_store_claim",
     ("Store one claim, written to this standard. One statement: split compound sentences. Readable alone: names "
     "in place of pronouns, and keep the date, version or condition the source gives. No stronger than its source: "
     "keep its scope and its hedges. Checkable: someone could describe evidence that would count against it. About "
     "the world, in the third person: the evidence record says who said it and the claim says what was said, so do "
     "not write \"the author wrote that...\". Name the speaker only when the statement itself is the fact, as with "
     "a person's or company's public statement. Faithful: it says what the source says and adds nothing. Not a "
     "duplicate: search the collection for it first, and link the existing claim instead of storing it again. A "
     "claim needs no evidence. Add link only when the evidence is already in this conversation and you have its id; "
     "never search or list evidence to find something to link. Pass claim_id in place of text to link evidence to "
     "an existing claim. link.quote is optional: include it only to point at a passage in a long document."),
     {"text": {"type": "string"}, "claim_id": _ID,
      "link": {"type": "object",
               "properties": {"evidence_id": _ID, "relation": {"type": "string", "enum": list(ledger.RELATIONS)},
                              "reason": {"type": "string"}, "quote": {"type": "string"}},
               "required": ["evidence_id", "relation", "reason"]}},
     []),
    (check_proof, "paranoy_check_proof",
     ("Check the structure of an argument before presenting it. proof = {question, steps: [{id, say, from: [record "
     "ids or earlier step ids], gap?}]}. Every step cites a record or an earlier step, or is marked gap. One step, "
     "the answer, is cited by no other. Returns problems and a readable tree. It does not check that the reasoning "
     "is right."),
     {"proof": {"type": "object"}},
     ["proof"]),
]

NAMES = [name for _fn, name, *_rest in SPECS]


def _has_key_material(value) -> bool:
    if isinstance(value, str):
        return bool(_NSEC.search(value))
    if isinstance(value, dict):
        return any(_has_key_material(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_key_material(item) for item in value)
    return False


def build(ctx, settings_factory, service_factory) -> list[dict]:
    """Tool specs for ``ctx.register_tool``. Handlers return JSON and never raise."""

    def make(fn, name):
        def handler(args, session_id=None, **kwargs):
            try:
                args = dict(args or {})
                if _has_key_material(args):
                    raise PluginError("A secret key is never passed to a tool.")
                settings = settings_factory()
                if not settings["ready"]:
                    raise PluginError("Paranoy is not set up yet. Run /paranoy status.")
                result = fn(service_factory(settings), settings, args, ctx)
                if name == "paranoy_store_evidence" and _text(args, "retrieved_via").lower() == "decision":
                    ground.end_decision(session_id)
            except PluginError as exc:
                result = {"error": str(exc)}
            except Exception:
                logger.exception("paranoy tool %s failed", name)
                result = {"error": "The Paranoy tool failed unexpectedly."}
            if isinstance(result, dict) and result.get("error"):
                state.journal("tool-error", tool=name, error=result["error"])
            return redact(json.dumps(result, ensure_ascii=False))

        return handler

    return [
        {
            "name": name,
            "handler": make(fn, name),
            "schema": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }
        for fn, name, description, properties, required in SPECS
    ]

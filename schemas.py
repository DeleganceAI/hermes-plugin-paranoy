"""Config defaults and tool argument shapes.

``plugin.yaml`` is what Hermes reads. Keep its ``config_schema`` and
``provides_tools`` aligned with this module. Tool arguments are plain
fields. None of them accept a signed kind 4707 request, a signature, or
a secret.
"""

from __future__ import annotations

import json

TERMS_VERSION = "1"
TERMS_SHA256 = "ef0c5a74783a3639172ab7dd1d7df764611bb70e29dccfb34823cc0a86154626"
SECRET_ENV = "PARANOY_BOT_SECRET_HEX"

MODES = ("reader", "contributor", "admin", "ledger")
CONTRIBUTOR_ROLES = (
    "Scout",
    "Claim Miner",
    "Linker",
    "Gap Finder",
    "Fool",
    "Ponder",
    "Ledger",
)

DEFAULTS = {
    "server_url": "https://paranoy.org",
    "collection": "",
    "collections": "",
    "mode": "reader",
    "roles": "",
    "owner": "",
    "report_to": "",
    "agent_name": "",
    "agent_about": "",
    "require_publish_approval": True,
    "allow_unattended_writes": False,
    "auto_sign_terms": True,
    "terms_version": TERMS_VERSION,
    "terms_sha256": TERMS_SHA256,
}

_STUB = (
    " Not implemented in this scaffold: nothing is signed and the server "
    "is not called."
)


def not_implemented(tool: str) -> str:
    """JSON string handlers return when the signing client cannot run."""
    return json.dumps(
        {
            "error": "not implemented",
            "tool": tool,
            "detail": (
                "Paranoy plugin scaffold: signing and live server calls "
                "are not implemented."
            ),
        }
    )


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    parameters: dict = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return {
        "name": name,
        "description": description.rstrip() + _STUB,
        "parameters": parameters,
    }


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


def _int(description: str) -> dict:
    return {"type": "integer", "description": description}


def _bool(description: str) -> dict:
    return {"type": "boolean", "description": description}


def _str_list(description: str) -> dict:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


READ_SCHEMAS = [
    _schema(
        "list_collections",
        "List Paranoy collections this key belongs to.",
        {},
    ),
    _schema(
        "fetch_url",
        "Fetch a public http(s) document as plain text. Nothing is stored.",
        {"url": _str("http(s) URL of the page or document.")},
        ["url"],
    ),
    _schema(
        "get_event",
        "Read one event by id. Page evidence text with offset until chars is covered.",
        {
            "id": _str("64-character hex event id."),
            "offset": _int("Evidence text window start. Default 0."),
            "max_chars": _int("Evidence text window length. Default 20000."),
        },
        ["id"],
    ),
    _schema(
        "search_claims",
        "Search claims in one collection before answering or writing.",
        {
            "collection": _str("Collection slug."),
            "text": _str("Question or statement to search for."),
            "method": _str('Search method: "vector" or "keyword".'),
            "limit": _int("Maximum results. Default 10, at most 200."),
        },
        ["collection", "text"],
    ),
    _schema(
        "list_claims",
        "List claims in one collection as a worklist.",
        {
            "collection": _str("Collection slug."),
            "sort": _str(
                "unsupported, unchallenged, contested, links, recent, or oldest."
            ),
            "limit": _int("Maximum results. Default 50, at most 200."),
            "offset": _int("Paging offset. Default 0."),
        },
        ["collection"],
    ),
    _schema(
        "get_links",
        "List links in one collection, optionally filtered by target, source, or relation.",
        {
            "collection": _str("Collection slug."),
            "to_id": _str("Links targeting this event id."),
            "from_id": _str("Links grounded in this evidence id."),
            "relation": _str("SUPPORTS, REBUTS, or QUALIFIES."),
            "limit": _int("Maximum results. Default 100, at most 1000."),
            "offset": _int("Paging offset. Default 0."),
        },
        ["collection"],
    ),
    _schema(
        "list_evidence",
        "List evidence metadata in one collection. Read the text with get_event.",
        {
            "collection": _str("Collection slug."),
            "ungrounded_only": _bool("Only documents no link cites yet."),
            "source": _str("Case-insensitive substring of the source field."),
            "limit": _int("Maximum results. Default 50, at most 500."),
            "offset": _int("Paging offset. Default 0."),
        },
        ["collection"],
    ),
    _schema(
        "search_contradictions",
        "List contradictions in one collection, with their claims.",
        {
            "collection": _str("Collection slug."),
            "claim": _str("Only contradictions this claim id belongs to."),
            "text": _str("Words that must appear in the reason."),
            "limit": _int("Maximum results. Default 20, at most 200."),
        },
        ["collection"],
    ),
    _schema(
        "check_proof",
        "Check the structure of a thinking proof. This is not a verdict.",
        {
            "collection": _str("Collection slug."),
            "proof": {
                "type": "object",
                "description": "Object with question and steps, as the Paranoy docs describe.",
            },
        },
        ["collection", "proof"],
    ),
]

WRITE_SCHEMAS = [
    _schema(
        "add_evidence",
        "Store one evidence document (kind 4701). The plugin will sign it later. "
        "Pass the full verbatim text, not a summary and not a pre-signed event.",
        {
            "collection": _str("Collection slug."),
            "text": _str("Full verbatim source text."),
            "url": _str("Source URL, when there is one."),
            "title": _str("Document title."),
            "source": _str("Publisher or site."),
            "archive_url": _str("Where the text was actually read."),
            "retrieved_via": _str('How the text was obtained, such as "Wayback".'),
        },
        ["collection", "text"],
    ),
    _schema(
        "add_claim",
        "Store one claim (kind 4702). The plugin will sign it later. "
        "Pass only the claim text.",
        {
            "collection": _str("Collection slug."),
            "text": _str("One checkable statement."),
        },
        ["collection", "text"],
    ),
    _schema(
        "add_link",
        "Store one evidence link (kind 4703). The plugin will sign it later. "
        "The quote must be an exact substring of the cited evidence.",
        {
            "collection": _str("Collection slug."),
            "from": _str("Evidence event id the quote comes from."),
            "to": _str("Claim, link, contradiction, or evidence id."),
            "relation": _str("SUPPORTS, REBUTS, or QUALIFIES."),
            "quote": _str("Exact substring of the evidence text."),
            "reason": _str("Why the quote bears on the target."),
        },
        ["collection", "from", "to", "relation", "quote", "reason"],
    ),
    _schema(
        "add_contradiction",
        "Store one contradiction (kind 4704). The plugin will sign it later. "
        "Name two or more claims and why they cannot all hold.",
        {
            "collection": _str("Collection slug."),
            "claims": _str_list("Two or more distinct claim ids."),
            "reason": _str("Why these claims cannot all hold."),
        },
        ["collection", "claims", "reason"],
    ),
]

GOVERNANCE_SCHEMAS = [
    _schema(
        "list_members",
        "List the roster of one collection and what this key may change.",
        {"collection": _str("Collection slug.")},
        ["collection"],
    ),
    _schema(
        "list_delegations",
        "List create-collection grants involving this key. This does not grant stewards.",
        {},
    ),
    _schema(
        "replace_members",
        "Replace the whole member list (kind 4705). A later version will fetch, "
        "merge, preview, and always ask for approval. A partial list removes "
        "everyone left out. The owner stays a member.",
        {
            "collection": _str("Collection slug."),
            "members": _str_list("Full list of member pubkeys, owner included."),
        },
        ["collection", "members"],
    ),
    _schema(
        "set_topic",
        "Set one collection's topic and public flag (kind 4709).",
        {
            "collection": _str("Collection slug."),
            "topic": _str("One line, at most 140 characters."),
            "public": _bool("Whether the collection is listed publicly."),
        },
        ["collection", "topic", "public"],
    ),
    _schema(
        "replace_admins",
        "Replace the whole admin list (kind 4710). Owner only. Leave the owner's "
        "pubkey out. A later version will fetch, merge, preview, and always ask "
        "for approval.",
        {
            "collection": _str("Collection slug."),
            "admins": _str_list("Full list of admin pubkeys, owner excluded."),
        },
        ["collection", "admins"],
    ),
    _schema(
        "create_collection",
        "Create a collection this key owns, or a kind 4712 collection for a "
        "grantor when this key is already a steward. Kind 4711 steward grants "
        "are not available here; a person signs those.",
        {
            "collection": _str("New collection slug."),
            "owner": _str(
                "Grantor pubkey for kind 4712. Omit when this key creates its own collection."
            ),
        },
        ["collection"],
    ),
    _schema(
        "replace_account_agents",
        "Replace the account agent list (kind 4713). This tool cannot change "
        "which key is the Ledger. A later version will fetch, merge, preview, "
        "and always ask for approval.",
        {
            "agents": _str_list("Full list of agent pubkeys on the account."),
        },
        ["agents"],
    ),
    _schema(
        "replace_collection_agents",
        "Replace which of the account's agents may work in one collection "
        "(kind 4714). A later version will fetch, merge, preview, and always "
        "ask for approval.",
        {
            "collection": _str("Collection slug."),
            "agents": _str_list("Full list of agent pubkeys for this collection."),
        },
        ["collection", "agents"],
    ),
]

READ_TOOL_NAMES = [schema["name"] for schema in READ_SCHEMAS]
WRITE_TOOL_NAMES = [schema["name"] for schema in WRITE_SCHEMAS]
GOVERNANCE_TOOL_NAMES = [schema["name"] for schema in GOVERNANCE_SCHEMAS]
ALL_TOOL_NAMES = READ_TOOL_NAMES + WRITE_TOOL_NAMES + GOVERNANCE_TOOL_NAMES

GOVERNANCE_MUTATION_NAMES = [
    name
    for name in GOVERNANCE_TOOL_NAMES
    if name not in {"list_members", "list_delegations"}
]

# Tools that do not need a pinned collection slug.
COLLECTION_FREE_TOOLS = frozenset(
    {
        "list_collections",
        "fetch_url",
        "list_delegations",
        "create_collection",
        "replace_account_agents",
    }
)

HOOKS = ("pre_llm_call", "pre_tool_call", "post_tool_call")
SKILLS = ("reader", "contributor", "admin", "ledger")

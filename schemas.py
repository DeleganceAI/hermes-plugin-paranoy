"""Tool argument shapes and config defaults. ``plugin.yaml`` must list the same tools."""

from __future__ import annotations

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
ROLE_INDEX = {name.casefold(): name for name in CONTRIBUTOR_ROLES}
DEFAULT_AGENT_NAME = "paranoy-agent"
DEFAULT_AGENT_ABOUT = "agent"
PROFILE_NAME_MAX = 64
PROFILE_ABOUT_MAX = 500

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
    "register_public_collections": "all",
    "auto_sign_terms": True,
    "terms_version": TERMS_VERSION,
    "terms_sha256": TERMS_SHA256,
}

READ_TOOLS = (
    "paranoy_list_collections",
    "paranoy_list_public_collections",
    "paranoy_list_members",
    "paranoy_list_delegations",
    "paranoy_search_claims",
    "paranoy_list_claims",
    "paranoy_list_evidence",
    "paranoy_get_event",
    "paranoy_get_links",
    "paranoy_search_contradictions",
    "paranoy_check_proof",
    "paranoy_fetch_url",
    "paranoy_status",
)
CONTENT_TOOLS = (
    "paranoy_add_evidence",
    "paranoy_add_claim",
    "paranoy_add_link",
    "paranoy_add_contradiction",
)
LOCAL_TOOLS = (
    "paranoy_log_reject",
    "paranoy_journal",
    "paranoy_set_active_role",
)
ADMIN_TOOLS = (
    "paranoy_set_topic",
    "paranoy_add_members",
    "paranoy_remove_members",
    "paranoy_set_admins",
    "paranoy_create_collection",
)
LEDGER_TOOLS = (
    "paranoy_update_overview",
    "paranoy_set_collection_agents",
    "paranoy_set_account_agents",
)
# Topic, members, and create are shared by admin and ledger mode.
SHARED_GOVERNANCE = (
    "paranoy_set_topic",
    "paranoy_add_members",
    "paranoy_remove_members",
    "paranoy_create_collection",
)
# Visible in every mode once a key is stored. Publishes kind 0 only.
PROFILE_TOOLS = ("paranoy_set_profile",)
# Agent versions of the /paranoy config commands. Visible in every mode once a
# key is stored. They change local plugin settings and publish nothing.
CONFIG_TOOLS = (
    "paranoy_set_collection",
    "paranoy_set_mode",
    "paranoy_set_roles",
    "paranoy_whoami",
)
# Builds Hermes cron jobs for one role. Visible once a key is stored.
JOB_TOOLS = ("paranoy_schedule_job",)
ALL_TOOL_NAMES = (
    READ_TOOLS
    + CONTENT_TOOLS
    + LOCAL_TOOLS
    + ADMIN_TOOLS
    + LEDGER_TOOLS
    + PROFILE_TOOLS
    + CONFIG_TOOLS
    + JOB_TOOLS
)

COLLECTION_FREE = frozenset(
    {
        "paranoy_list_collections",
        "paranoy_list_public_collections",
        "paranoy_list_delegations",
        "paranoy_fetch_url",
        "paranoy_get_event",
        "paranoy_status",
        "paranoy_set_active_role",
        "paranoy_journal",
        "paranoy_log_reject",
        "paranoy_update_overview",
        "paranoy_set_account_agents",
        "paranoy_create_collection",
        "paranoy_set_profile",
        *CONFIG_TOOLS,
        *JOB_TOOLS,
    }
)
EXPLICIT_OWNER_TOOLS = frozenset(
    {"paranoy_add_claim", "paranoy_add_link", "paranoy_add_contradiction"}
)
ALWAYS_APPROVE = frozenset(
    {
        "paranoy_add_members",
        "paranoy_remove_members",
        "paranoy_set_admins",
        "paranoy_set_collection_agents",
        "paranoy_set_account_agents",
    }
)
APPROVAL_WRITES = frozenset(
    CONTENT_TOOLS + ("paranoy_set_topic", "paranoy_create_collection") + PROFILE_TOOLS
)
# Setting changes a person approves, like a write. Nothing is sent to the server.
CONFIG_APPROVAL = frozenset(
    {"paranoy_set_collection", "paranoy_set_mode", "paranoy_set_roles"}
)
# Every /paranoy subcommand and the agent tool that does the same thing.
# keygen and register are terminal-only. keygen creates the secret, and
# register retries public-collection registration without rotating it.
# The agent never creates or handles the secret.
SLASH_TOOLS = {
    "status": "paranoy_status",
    "public": "paranoy_list_public_collections",
    "collection": "paranoy_set_collection",
    "mode": "paranoy_set_mode",
    "roles": "paranoy_set_roles",
    "whoami": "paranoy_whoami",
    "profile": "paranoy_whoami",
    "profile name": "paranoy_set_profile",
    "profile about": "paranoy_set_profile",
    "profile publish": "paranoy_set_profile",
    "job": "paranoy_schedule_job",
    "job create": "paranoy_schedule_job",
    "job list": "paranoy_schedule_job",
    "job remove": "paranoy_schedule_job",
}
TERMINAL_ONLY = ("keygen", "register")
ROLE_CONTENT = {
    "Scout": {"paranoy_add_evidence", "paranoy_add_claim"},
    "Claim Miner": {"paranoy_add_claim"},
    "Linker": {"paranoy_add_link"},
    "Gap Finder": {"paranoy_add_evidence", "paranoy_add_link"},
    "Fool": {"paranoy_add_evidence", "paranoy_add_link"},
    "Ponder": set(),
    "Ledger": set(),
}
HOOKS = ("pre_llm_call", "pre_tool_call", "post_tool_call")
SKILLS = ("reader", "contributor", "admin", "ledger", "setup", "cadence")
EVIDENCE_PAGE_CHARS = 20000
EVIDENCE_CHAR_CAP = 200000

COLLECTION_ARG = (
    "Collection slug for this call only. Defaults to the pinned collection. "
    "The pin does not change. This key must be a member of it."
)
RETRIEVAL_NOTE = (
    "Ranks, distances, and support counts are retrieval aids, not verdicts or confidence."
)


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    parameters: dict = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return {"name": name, "description": description, "parameters": parameters}


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


def _int(description: str) -> dict:
    return {"type": "integer", "description": description}


def _bool(description: str) -> dict:
    return {"type": "boolean", "description": description}


def _str_list(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def _pubkeys(description: str) -> dict:
    return _str_list(description)


SCHEMAS = {
    "paranoy_list_collections": _schema(
        "paranoy_list_collections",
        "List Paranoy collections this key belongs to. This is not the public catalog.",
        {},
    ),
    "paranoy_list_public_collections": _schema(
        "paranoy_list_public_collections",
        "List public Paranoy collections from the server catalog and cache them. "
        "This is not paranoy_list_collections, which is only collections this key belongs to. "
        "A public collection's records stay private until this key is a member. "
        "To read or contribute, pass collection on the other tools once member is true.",
        {},
    ),
    "paranoy_list_members": _schema(
        "paranoy_list_members",
        "List the roster of one collection and whether this key may change members or admins.",
        {"collection": _str(COLLECTION_ARG)},
    ),
    "paranoy_list_delegations": _schema(
        "paranoy_list_delegations",
        "List create-collection grants involving this key. This does not grant or change stewards.",
        {},
    ),
    "paranoy_search_claims": _schema(
        "paranoy_search_claims",
        "Search claims in one collection. " + RETRIEVAL_NOTE,
        {
            "collection": _str(COLLECTION_ARG),
            "text": _str("Question or statement to search for."),
            "method": _str('Search method: "vector" or "keyword".'),
            "limit": _int("Maximum results, from 1 to 200."),
        },
        ["text"],
    ),
    "paranoy_list_claims": _schema(
        "paranoy_list_claims",
        "List claims in one collection. " + RETRIEVAL_NOTE,
        {
            "collection": _str(COLLECTION_ARG),
            "sort": _str("unsupported, unchallenged, contested, links, recent, or oldest."),
            "limit": _int("Maximum results, from 1 to 200."),
            "offset": _int("Paging offset."),
        },
    ),
    "paranoy_list_evidence": _schema(
        "paranoy_list_evidence",
        "List evidence metadata in one collection. Read the text with paranoy_get_event.",
        {
            "collection": _str(COLLECTION_ARG),
            "ungrounded_only": _bool("Only documents no link cites yet."),
            "source": _str("Case-insensitive substring of the source field."),
            "limit": _int("Maximum results, from 1 to 500."),
            "offset": _int("Paging offset."),
        },
    ),
    "paranoy_get_event": _schema(
        "paranoy_get_event",
        "Read one event by id. Evidence text is paged automatically up to a cap.",
        {"id": _str("64-character hex event id.")},
        ["id"],
    ),
    "paranoy_get_links": _schema(
        "paranoy_get_links",
        "List links in one collection, with full quotes.",
        {
            "collection": _str(COLLECTION_ARG),
            "to_id": _str("Links targeting this event id."),
            "from_id": _str("Links grounded in this evidence id."),
            "relation": _str("SUPPORTS, REBUTS, or QUALIFIES."),
            "limit": _int("Maximum results, from 1 to 1000."),
            "offset": _int("Paging offset."),
        },
    ),
    "paranoy_search_contradictions": _schema(
        "paranoy_search_contradictions",
        "List contradictions in one collection. The ledger does not resolve them. " + RETRIEVAL_NOTE,
        {
            "collection": _str(COLLECTION_ARG),
            "claim": _str("Only contradictions this claim id belongs to."),
            "text": _str("Words that must appear in the reason."),
            "limit": _int("Maximum results, from 1 to 200."),
        },
    ),
    "paranoy_check_proof": _schema(
        "paranoy_check_proof",
        "Check the structure of a thinking proof. This is not a verdict.",
        {
            "collection": _str(COLLECTION_ARG),
            "proof": {"type": "object", "description": "Object with question and steps."},
        },
        ["proof"],
    ),
    "paranoy_fetch_url": _schema(
        "paranoy_fetch_url",
        "Fetch a public http(s) document through the Paranoy server. Nothing is stored.",
        {"url": _str("http(s) URL.")},
        ["url"],
    ),
    "paranoy_status": _schema(
        "paranoy_status",
        "Show mode, collections, roles, npub, membership, configured terms, and steward grants. Never the secret.",
        {},
    ),
    "paranoy_add_evidence": _schema(
        "paranoy_add_evidence",
        "Store full verbatim evidence (kind 4701). The plugin signs it. Pass fetch true to load the URL through the server first.",
        {
            "collection": _str(COLLECTION_ARG),
            "text": _str("Full verbatim source text. Omit when fetch is true."),
            "url": _str("Source URL."),
            "title": _str("Document title."),
            "source": _str("Publisher or site."),
            "archive_url": _str("Where the text was actually read."),
            "retrieved_via": _str("How the text was obtained."),
            "fetch": _bool("When true, and a url is set, load the text with the server fetch_url tool first."),
        },
    ),
    "paranoy_add_claim": _schema(
        "paranoy_add_claim",
        "Store one claim (kind 4702). would_be_rebutted_by is required and is not stored. Optional link fields publish a kind 4703 to existing evidence in the same batch.",
        {
            "collection": _str(COLLECTION_ARG),
            "text": _str("One checkable statement."),
            "would_be_rebutted_by": _str("What would show the claim is false. Required. Not stored."),
            "from": _str("Existing evidence id when also publishing a link."),
            "quote": _str("Exact substring of that evidence."),
            "relation": _str("SUPPORTS, REBUTS, or QUALIFIES."),
            "reason": _str("Why the quote bears on the claim."),
            "owner_asked": _bool("Required in ledger mode."),
        },
        ["text", "would_be_rebutted_by"],
    ),
    "paranoy_add_link": _schema(
        "paranoy_add_link",
        "Store one link (kind 4703). The quote is checked locally against the evidence before anything is sent.",
        {
            "collection": _str(COLLECTION_ARG),
            "from": _str("Evidence event id."),
            "to": _str("Target event id."),
            "relation": _str("SUPPORTS, REBUTS, or QUALIFIES."),
            "quote": _str("Substring of the evidence, aside from case, whitespace, quotes, dashes, soft hyphens, and fi/fl ligatures."),
            "reason": _str("Why the quote bears on the target."),
            "owner_asked": _bool("Required in ledger mode."),
        },
        ["from", "to", "relation", "quote", "reason"],
    ),
    "paranoy_add_contradiction": _schema(
        "paranoy_add_contradiction",
        "Store one contradiction (kind 4704). This does not resolve it or score either claim.",
        {
            "collection": _str(COLLECTION_ARG),
            "claims": _str_list("Two or more distinct claim ids."),
            "reason": _str("Why these claims cannot all hold."),
            "owner_asked": _bool("Required in ledger mode."),
        },
        ["claims", "reason"],
    ),
    "paranoy_log_reject": _schema(
        "paranoy_log_reject",
        "Append a local reject record. Nothing is written to the ledger.",
        {
            "what": _str("What was skipped."),
            "reason": _str("One-sentence reason."),
            "collection": _str("Collection slug, if any."),
        },
        ["what", "reason"],
    ),
    "paranoy_journal": _schema(
        "paranoy_journal",
        "Append a local note. Nothing is written to the ledger.",
        {"note": _str("The note to keep locally.")},
        ["note"],
    ),
    "paranoy_set_active_role": _schema(
        "paranoy_set_active_role",
        "Set the active contributor role. It must be one of the configured roles. This is explicit and is not inferred from prose.",
        {"role": _str("Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, or Ledger.")},
        ["role"],
    ),
    "paranoy_set_topic": _schema(
        "paranoy_set_topic",
        "Set one collection's topic and public flag (kind 4709). Owner or admin.",
        {
            "collection": _str("Collection slug."),
            "topic": _str("One line, at most 140 characters."),
            "public": _bool("Whether the collection is listed publicly."),
        },
        ["topic", "public"],
    ),
    "paranoy_add_members": _schema(
        "paranoy_add_members",
        "Add pubkeys to a collection. The plugin fetches the roster, previews the change, and publishes the complete kind 4705 list. Do not pass a replacement list.",
        {
            "collection": _str("Collection slug."),
            "add": _pubkeys("Pubkeys to add."),
        },
        ["add"],
    ),
    "paranoy_remove_members": _schema(
        "paranoy_remove_members",
        "Remove pubkeys from a collection. The owner cannot be removed. Wiping everyone except the owner requires confirm_owner_only. The current roster is fetched first.",
        {
            "collection": _str("Collection slug."),
            "remove": _pubkeys("Pubkeys to remove."),
            "confirm_owner_only": _bool("Allow a roster that contains only the owner."),
        },
        ["remove"],
    ),
    "paranoy_set_admins": _schema(
        "paranoy_set_admins",
        "Add or remove admins (kind 4710). Owner only. The owner is left out of the admin list. The current list is fetched first.",
        {
            "collection": _str("Collection slug."),
            "add": _pubkeys("Pubkeys to add as admins."),
            "remove": _pubkeys("Admin pubkeys to remove."),
        },
    ),
    "paranoy_create_collection": _schema(
        "paranoy_create_collection",
        "Create a collection this key will own, or a kind 4712 collection when owner is a grantor who has authorized this key. Optional topic and initial members follow the creating event.",
        {
            "collection": _str("New collection slug."),
            "topic": _str("Topic, at most 140 characters."),
            "public": _bool("Whether the new collection is public."),
            "owner": _str("Grantor pubkey for kind 4712. Omit when this key will own the collection."),
            "members": _pubkeys("Initial member pubkeys besides the owner."),
        },
        ["collection"],
    ),
    "paranoy_update_overview": _schema(
        "paranoy_update_overview",
        "Write the local overview file in plugin data. Nothing is published to the ledger.",
        {
            "text": _str("Markdown to store."),
            "replace": _bool("Replace the file. Otherwise the text is appended."),
        },
        ["text"],
    ),
    "paranoy_set_collection_agents": _schema(
        "paranoy_set_collection_agents",
        "Add or remove agents for one collection (kind 4714). The current list is fetched and the full list is published. A replacement list from the model is not published as-is.",
        {
            "collection": _str("Collection slug."),
            "add": _pubkeys("Agent pubkeys to add."),
            "remove": _pubkeys("Agent pubkeys to remove."),
        },
    ),
    "paranoy_set_account_agents": _schema(
        "paranoy_set_account_agents",
        "Add or remove account agents (kind 4713). The ledger field stays the fetched value. This tool cannot change which key is the Ledger.",
        {
            "add": _pubkeys("Agent pubkeys to add."),
            "remove": _pubkeys("Agent pubkeys to remove."),
        },
    ),
    "paranoy_set_profile": _schema(
        "paranoy_set_profile",
        "Set and publish this agent's Paranoy profile (kind 0). Unset fields keep the current "
        "setting. The new values are saved in the plugin settings, then the profile is signed "
        "and published. A person approves it first. Say plainly in about that this key is an "
        "agent and who runs it, because Paranoy does not check whether a key is a person or an "
        "agent. Call it with no arguments to republish the current profile.",
        {
            "name": _str(f"Display name, one line, at most {PROFILE_NAME_MAX} characters. Cannot be empty."),
            "about": _str(
                f"About text, at most {PROFILE_ABOUT_MAX} characters. State that this key is an agent."
            ),
            "publish": _bool("Publish after saving. Default true. False only saves the settings."),
        },
    ),
    "paranoy_set_collection": _schema(
        "paranoy_set_collection",
        "Change which collection the other Paranoy tools read from and write to, the same as "
        "the user typing /paranoy collection <slug>. The slug must be a collection this key is "
        "a member of; get slugs from paranoy_list_collections. A person approves the switch. "
        "It does not change the mode or roles, and nothing is published.",
        {"slug": _str("Collection slug from paranoy_list_collections.")},
        ["slug"],
    ),
    "paranoy_set_mode": _schema(
        "paranoy_set_mode",
        "Change the Paranoy mode, the same as /paranoy mode <mode>. Modes decide which tools "
        "are visible, including writes, so a person approves the change. The result lists the "
        "tools that become visible or hidden on the next turn. Nothing is published.",
        {"mode": _str("reader, contributor, admin, or ledger.")},
        ["mode"],
    ),
    "paranoy_set_roles": _schema(
        "paranoy_set_roles",
        "Replace the configured contributor roles, the same as /paranoy roles <roles>. A person "
        "approves the change. The result lists the tools that become visible or hidden on the "
        "next turn. To switch between roles that are already configured, use "
        "paranoy_set_active_role instead. Nothing is published.",
        {
            "roles": _str_list(
                "Roles from Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, Ledger. "
                "A comma-separated string is also accepted."
            )
        },
        ["roles"],
    ),
    "paranoy_schedule_job": _schema(
        "paranoy_schedule_job",
        "Build a recurring Paranoy job that works as one contributor role in one collection, "
        "the same as /paranoy job. action create returns the exact arguments for Hermes' "
        "cronjob_manage tool; call cronjob_manage with them to schedule it, because this plugin "
        "cannot schedule jobs itself. The job prompt passes the collection on every call, never "
        "switches the pin, stays inside the role, dedupes, and stops at the target count. "
        "create and remove need a person's approval and are refused inside cron runs. list "
        "shows the jobs this plugin built.",
        {
            "action": _str("create, list, or remove."),
            "role": _str("Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, or Ledger."),
            "collection": _str("Collection slug the job works in. The pin does not change."),
            "target": _int("Items to add per run, from 1 to 500, for example 50."),
            "cadence": _str("hourly, daily, an interval like 'every 2h', or a 5-field cron expression."),
            "focus": _str("Optional topic or focus for the job, at most 500 characters."),
            "name": _str("Job name. Defaults to paranoy-<role>-<collection>. Required for remove."),
        },
        ["action"],
    ),
    "paranoy_whoami": _schema(
        "paranoy_whoami",
        "Show this agent's npub and its saved profile name and about, the same as /paranoy "
        "whoami and /paranoy profile. No server call and no secret.",
        {},
    ),
}

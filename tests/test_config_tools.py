"""Agent tools for the /paranoy config commands, and slash/tool parity."""

import json
import unittest

from tests.support import FakeService, load_plugin
from tests.test_profile import Ctx, KeyedTestCase

plugin = load_plugin()
gates = plugin.gates
schemas = plugin.schemas
commands = plugin.commands
config_tools = plugin.tools.config
PluginError = plugin.client.PluginError


def listing(service, *extra):
    """Rows like the live server: role is null, membership shows in the lists."""
    rows = [
        {
            "slug": "super-collection",
            "owner": "someone-else",
            "members": [service.pubkey],
            "admins": [],
            "role": None,
            "topic": "Everything i work on",
            "public": False,
            "evidence": 224,
            "claims": 369,
            "links": 838,
            "contradictions": 5,
        },
        {
            "slug": "ufo-uap-worldview",
            "owner": "someone-else",
            "members": [service.pubkey],
            "admins": [],
            "role": None,
            "topic": "This is a collection to model the world of UAP/UFO realities",
            "public": True,
            "evidence": 86,
            "claims": 144,
            "links": 564,
            "contradictions": 3,
        },
        {"slug": "beta", "role": "member", "topic": "b"},
        {"slug": "not-mine", "owner": "x", "members": ["y"], "role": None},
        *extra,
    ]
    return {"result": rows}


class SetCollectionTests(KeyedTestCase):
    def run_tool(self, ctx, args, service=None):
        service = service or FakeService(plugin)
        service.responses["list_collections"] = listing(service)
        settings = gates.read_settings(ctx)
        return config_tools.set_collection(service, settings, args, ctx.set_config), service

    def test_success_switches_and_reports_the_row(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        result, service = self.run_tool(ctx, {"slug": "ufo-uap-worldview"})
        self.assertEqual(ctx.settings["collection"], "ufo-uap-worldview")
        self.assertEqual(result["previous"], "super-collection")
        self.assertEqual(result["collection"], "ufo-uap-worldview")
        self.assertTrue(result["changed"])
        self.assertIn("UAP", result["topic"])
        self.assertEqual(result["counts"], {"evidence": 86, "claims": 144, "links": 564, "contradictions": 3})
        self.assertEqual(ctx.settings["mode"], "reader")
        self.assertIn("Mode and roles did not change", result["note"])
        self.assertEqual(service.published, [])
        self.assertEqual(service.calls, [("list_collections", {})])
        resolved = gates.resolve_collection(gates.read_settings(ctx), "paranoy_search_claims", {})
        self.assertEqual(resolved, "ufo-uap-worldview")

    def test_non_member_is_refused_with_the_slugs(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        result, _ = self.run_tool(ctx, {"slug": "not-mine"})
        self.assertIn("not a member of not-mine", result["error"])
        self.assertEqual(result["available"], ["beta", "super-collection", "ufo-uap-worldview"])
        self.assertIn("ufo-uap-worldview", result["error"])
        self.assertEqual(ctx.settings["collection"], "super-collection")
        result, _ = self.run_tool(ctx, {"slug": "missing"})
        self.assertIn("error", result)
        self.assertEqual(ctx.settings["collection"], "super-collection")

    def test_admin_managed_set_is_enforced(self):
        ctx = Ctx(mode="admin", collections="beta, gamma")
        with self.assertRaises(PluginError) as caught:
            self.run_tool(ctx, {"slug": "ufo-uap-worldview"})
        self.assertIn("managed set", str(caught.exception))
        self.assertIn("user", str(caught.exception))
        self.assertNotIn("collection", ctx.settings)
        blocked = gates.pre_tool_directive(ctx, "paranoy_set_collection", {"slug": "ufo-uap-worldview"})
        self.assertEqual(blocked["action"], "block")
        self.assertIn("managed set", blocked["message"])
        result, _ = self.run_tool(ctx, {"slug": "beta"})
        self.assertEqual(result["collection"], "beta")
        open_admin = Ctx(mode="admin")
        result, _ = self.run_tool(open_admin, {"slug": "ufo-uap-worldview"})
        self.assertEqual(open_admin.settings["collection"], "ufo-uap-worldview")

    def test_bad_slug_is_refused_before_any_call(self):
        ctx = Ctx(mode="reader", collection="pump")
        service = FakeService(plugin)
        for bad in ("", "UFO", "-x", "a b", "../etc"):
            with self.assertRaises(PluginError):
                config_tools.set_collection(service, gates.read_settings(ctx), {"slug": bad}, ctx.set_config)
            directive = gates.pre_tool_directive(ctx, "paranoy_set_collection", {"slug": bad})
            self.assertEqual(directive["action"], "block")
        self.assertEqual(service.calls, [])
        self.assertEqual(ctx.settings["collection"], "pump")

    def test_listing_failure_changes_nothing(self):
        ctx = Ctx(mode="reader", collection="pump")
        service = FakeService(plugin, responses={"list_collections": {"error": "down"}})
        with self.assertRaises(PluginError):
            config_tools.set_collection(service, gates.read_settings(ctx), {"slug": "beta"}, ctx.set_config)
        self.assertEqual(ctx.settings["collection"], "pump")

    def test_approval_is_required_and_not_a_ledger_write(self):
        self.assertIn("paranoy_set_collection", schemas.CONFIG_APPROVAL)
        self.assertNotIn("paranoy_set_collection", schemas.APPROVAL_WRITES)
        self.assertNotIn("paranoy_set_collection", schemas.ALWAYS_APPROVE)
        ctx = Ctx(mode="reader", collection="super-collection")
        directive = gates.pre_tool_directive(ctx, "paranoy_set_collection", {"slug": "ufo-uap-worldview"})
        self.assertEqual(directive["action"], "approve")
        self.assertIn("super-collection", directive["message"])
        self.assertIn("ufo-uap-worldview", directive["message"])
        self.assertIn("nothing is published", directive["message"])
        self.assertNotIn("write", directive["message"].lower())
        quiet = Ctx(mode="reader", collection="pump", require_publish_approval=False)
        self.assertIsNone(gates.pre_tool_directive(quiet, "paranoy_set_collection", {"slug": "beta"}))

    def test_cron_is_blocked_unless_allowed(self):
        cron = Ctx(cron=True, mode="reader", collection="pump")
        blocked = gates.pre_tool_directive(cron, "paranoy_set_collection", {"slug": "beta"})
        self.assertEqual(blocked["action"], "block")
        self.assertIn("cron", blocked["message"])
        allowed = Ctx(cron=True, mode="reader", collection="pump", allow_unattended_writes=True)
        self.assertIsNone(gates.pre_tool_directive(allowed, "paranoy_set_collection", {"slug": "beta"}))

    def test_description_says_what_it_changes(self):
        text = schemas.SCHEMAS["paranoy_set_collection"]["description"]
        self.assertIn("read from and write to", text)
        self.assertIn("does not change the mode or roles", text)
        self.assertEqual(schemas.SCHEMAS["paranoy_set_collection"]["parameters"]["required"], ["slug"])

    def test_registered_handler_saves_through_ctx(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        service = FakeService(plugin)
        service.responses["list_collections"] = listing(service)
        plugin.register(ctx)
        original = plugin.ParanoyClient, plugin.load_secret_hex
        plugin.ParanoyClient = lambda server, secret: service
        plugin.load_secret_hex = lambda: "11" * 32
        try:
            out = json.loads(ctx.tools["paranoy_set_collection"]["handler"]({"slug": "ufo-uap-worldview"}))
        finally:
            plugin.ParanoyClient, plugin.load_secret_hex = original
        self.assertEqual(out["collection"], "ufo-uap-worldview")
        self.assertEqual(ctx.settings["collection"], "ufo-uap-worldview")


class VisibilityTests(KeyedTestCase):
    def test_reader_with_and_without_a_pin(self):
        for pin in ("", "super-collection"):
            reader = gates.read_settings(Ctx(mode="reader", collection=pin))
            for name in (
                "paranoy_set_collection",
                "paranoy_set_mode",
                "paranoy_set_roles",
                "paranoy_whoami",
                "paranoy_list_collections",
                "paranoy_status",
            ):
                self.assertTrue(gates.tool_visible(reader, name), (pin, name))
                self.assertIsNone(gates.resolve_collection(reader, name, {}))

    def test_every_mode_needs_only_a_key(self):
        for mode in schemas.MODES:
            for name in schemas.CONFIG_TOOLS:
                self.assertTrue(gates.tool_visible({"mode": mode, "key_configured": True}, name))
                self.assertFalse(gates.tool_visible({"mode": mode, "key_configured": False}, name))
            self.assertIn(name, schemas.COLLECTION_FREE)

    def test_registered_check_fn(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        plugin.register(ctx)
        for name in schemas.CONFIG_TOOLS:
            self.assertTrue(ctx.tools[name]["check_fn"](), name)


class ModeAndRolesTests(KeyedTestCase):
    def test_set_mode_lists_tools_for_next_turn(self):
        ctx = Ctx(mode="reader", collection="pump", roles="Scout")
        result = config_tools.set_mode(None, gates.read_settings(ctx), {"mode": "Contributor"}, ctx.set_config)
        self.assertEqual(ctx.settings["mode"], "contributor")
        self.assertEqual(result["previous"], "reader")
        self.assertIn("paranoy_add_evidence", result["newly_visible"])
        self.assertIn("paranoy_add_evidence", result["visible_next_turn"])
        after = gates.read_settings(ctx)
        self.assertEqual(sorted(result["visible_next_turn"]), sorted(commands.visible_tools(after)))
        with self.assertRaises(PluginError):
            config_tools.set_mode(None, gates.read_settings(ctx), {"mode": "root"}, ctx.set_config)
        self.assertEqual(ctx.settings["mode"], "contributor")

    def test_set_roles_accepts_list_or_string(self):
        ctx = Ctx(mode="contributor", collection="pump")
        result = config_tools.set_roles(None, gates.read_settings(ctx), {"roles": ["linker"]}, ctx.set_config)
        self.assertEqual(ctx.settings["roles"], "Linker")
        self.assertEqual(result["active_role"], "Linker")
        self.assertIn("paranoy_add_link", result["newly_visible"])
        result = config_tools.set_roles(
            None, gates.read_settings(ctx), {"roles": "Scout, claim miner"}, ctx.set_config
        )
        self.assertEqual(ctx.settings["roles"], "Scout, Claim Miner")
        self.assertIn("paranoy_add_link", result["hidden_next_turn"])
        with self.assertRaises(PluginError):
            config_tools.set_roles(None, gates.read_settings(ctx), {"roles": "Scout, Wizard"}, ctx.set_config)
        self.assertEqual(ctx.settings["roles"], "Scout, Claim Miner")

    def test_mode_and_roles_need_approval_and_block_in_cron(self):
        for name, args in (("paranoy_set_mode", {"mode": "ledger"}), ("paranoy_set_roles", {"roles": ["Scout"]})):
            directive = gates.pre_tool_directive(Ctx(mode="reader"), name, args)
            self.assertEqual(directive["action"], "approve", name)
            cron = gates.pre_tool_directive(Ctx(cron=True, mode="reader"), name, args)
            self.assertEqual(cron["action"], "block", name)
            allowed = Ctx(cron=True, mode="reader", allow_unattended_writes=True)
            self.assertIsNone(gates.pre_tool_directive(allowed, name, args))
        bad = gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_set_mode", {"mode": "root"})
        self.assertEqual(bad["action"], "block")
        bad = gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_set_roles", {"roles": "Wizard"})
        self.assertEqual(bad["action"], "block")

    def test_read_only_tools_need_no_approval(self):
        for name in ("paranoy_whoami", "paranoy_status", "paranoy_list_collections"):
            self.assertIsNone(gates.pre_tool_directive(Ctx(mode="reader"), name, {}), name)
            self.assertIsNone(gates.pre_tool_directive(Ctx(cron=True, mode="reader"), name, {}), name)

    def test_whoami_returns_npub_and_profile(self):
        service = FakeService(plugin)
        result = config_tools.whoami(service, gates.read_settings(Ctx(agent_name="loraunna")), {}, None)
        self.assertEqual(result["npub"], service.npub)
        self.assertEqual(result["profile"]["name"], "loraunna")
        self.assertNotIn("nsec1", json.dumps(result))

    def test_profile_tool_can_save_without_publishing(self):
        ctx = Ctx()
        service = FakeService(plugin)
        result = plugin.tools.profile.set_profile(
            service, gates.read_settings(ctx), {"name": "loraunna", "publish": False}, ctx.set_config
        )
        self.assertFalse(result["published"])
        self.assertEqual(ctx.settings["agent_name"], "loraunna")
        self.assertEqual(service.published, [])


class SlashParityTests(KeyedTestCase):
    def test_every_slash_command_has_a_tool(self):
        """A new /paranoy subcommand fails here until it gets an agent tool."""
        commands_seen = set(gates.SLASH_COMMANDS)
        self.assertEqual(set(schemas.TERMINAL_ONLY), {"keygen"})
        for command in commands_seen - set(schemas.TERMINAL_ONLY):
            self.assertIn(command, schemas.SLASH_TOOLS, command)
        for sub in gates.PROFILE_SUBCOMMANDS:
            key = ("profile " + sub).strip()
            self.assertIn(key, schemas.SLASH_TOOLS, key)
        for key, tool in schemas.SLASH_TOOLS.items():
            self.assertIn(key.split()[0], commands_seen, key)
            self.assertIn(tool, schemas.ALL_TOOL_NAMES, key)
            self.assertTrue(
                gates.tool_visible({"mode": "reader", "key_configured": True, "collection": ""}, tool),
                tool,
            )
        for name in schemas.TERMINAL_ONLY:
            self.assertNotIn(name, schemas.SLASH_TOOLS)
        self.assertIn("terminal", gates.slash_result(Ctx(), "keygen"))

    def test_slash_and_tool_share_validation(self):
        ctx = Ctx(mode="reader", collection="pump")
        self.assertIn("Mode must be", gates.slash_result(ctx, "mode root"))
        self.assertIn("Roles must be", gates.slash_result(ctx, "roles Wizard"))
        self.assertIn("Collection slugs", gates.slash_result(ctx, "collection Bad Slug"))
        out = gates.slash_result(ctx, "mode contributor")
        self.assertTrue(out.startswith("mode: contributor"))
        self.assertEqual(ctx.settings["mode"], "contributor")
        out = gates.slash_result(ctx, "roles scout")
        self.assertTrue(out.startswith("roles: Scout"))
        self.assertIn("Visible from the next turn", out)
        admin = Ctx(mode="admin", collections="beta")
        self.assertIn("managed set", gates.slash_result(admin, "collection other"))
        self.assertNotIn("collection", admin.settings)

    def test_slash_collection_checks_membership_but_lets_the_user_decide(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        service = FakeService(plugin)
        service.responses["list_collections"] = listing(service)
        out = gates.slash_result(ctx, "collection ufo-uap-worldview", lambda settings: service)
        self.assertEqual(out, "collection: ufo-uap-worldview")
        out = gates.slash_result(ctx, "collection not-mine", lambda settings: service)
        self.assertIn("not listed as a member", out)
        self.assertEqual(ctx.settings["collection"], "not-mine")
        offline = Ctx(mode="reader")
        out = gates.slash_result(offline, "collection pump")
        self.assertIn("not checked", out)
        self.assertEqual(offline.settings["collection"], "pump")


class SetupHintCollectionTests(KeyedTestCase):
    def context(self, **settings):
        return gates.prompt_context(gates.read_settings(Ctx(**settings)))

    def test_no_collection_hint_offers_the_tool(self):
        text = self.context(mode="reader")
        self.assertIn("paranoy_set_collection", text)
        self.assertIn("paranoy_list_collections", text)
        self.assertIn("approves", text)
        self.assertIn("Mode and roles change only with the user's approval", text)
        self.assertNotIn("You cannot pin a collection", text)

    def test_complete_setup_mentions_switching(self):
        plugin.state.update_state(profile_published_at="2026-01-01T00:00:00Z")
        text = self.context(mode="reader", collection="super-collection")
        self.assertIn("paranoy_set_collection", text)
        self.assertIn("Mode and roles change only with the user's approval", text)

    def test_contributor_without_roles_offers_set_roles(self):
        text = self.context(mode="contributor", collection="pump")
        self.assertIn("paranoy_set_roles", text)


if __name__ == "__main__":
    unittest.main()

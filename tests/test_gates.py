import tempfile
import unittest
from pathlib import Path

from tests.support import load_plugin

plugin = load_plugin()
gates = plugin.gates
schemas = plugin.schemas


def settings(**extra):
    base = {
        "mode": "reader",
        "collection": "pump",
        "collections": "",
        "managed": [],
        "roles_list": ["Scout"],
        "active_role": "Scout",
        "key_configured": True,
        "require_publish_approval": True,
        "allow_unattended_writes": False,
    }
    base.update(extra)
    return base


class PinTests(unittest.TestCase):
    def test_reader_and_contributor_default_to_the_pin(self):
        for mode in ("reader", "contributor"):
            resolved = gates.resolve_collection(
                settings(mode=mode), "paranoy_search_claims", {}
            )
            self.assertEqual(resolved, "pump")
            # A per-call collection targets that collection; the pin is untouched.
            pinned = settings(mode=mode)
            self.assertEqual(
                gates.resolve_collection(pinned, "paranoy_search_claims", {"collection": "other"}),
                "other",
            )
            self.assertEqual(pinned["collection"], "pump")
            with self.assertRaises(plugin.client.PluginError):
                gates.resolve_collection(
                    settings(mode=mode, collection=""), "paranoy_search_claims", {}
                )
            with self.assertRaises(plugin.client.PluginError):
                gates.resolve_collection(pinned, "paranoy_search_claims", {"collection": "Bad Slug"})

    def test_list_collections_is_not_pinned(self):
        self.assertIsNone(
            gates.resolve_collection(settings(collection=""), "paranoy_list_collections", {})
        )
        self.assertEqual(
            gates.resolve_collection(
                settings(),
                "paranoy_list_collections",
                {"collection": "other"},
            ),
            "other",
        )

    def test_admin_must_stay_in_the_managed_set(self):
        admin = settings(mode="admin", collection="", managed=["alpha", "beta"])
        self.assertEqual(
            gates.resolve_collection(admin, "paranoy_set_topic", {"collection": "alpha"}),
            "alpha",
        )
        with self.assertRaises(plugin.client.PluginError):
            gates.resolve_collection(admin, "paranoy_set_topic", {"collection": "other"})

    def test_ledger_uses_a_named_collection_or_any_argument(self):
        named = settings(mode="ledger", collection="pump")
        self.assertEqual(gates.resolve_collection(named, "paranoy_search_claims", {}), "pump")
        self.assertEqual(
            gates.resolve_collection(named, "paranoy_search_claims", {"collection": "other"}),
            "other",
        )
        open_ledger = settings(mode="ledger", collection="")
        self.assertEqual(
            gates.resolve_collection(open_ledger, "paranoy_search_claims", {"collection": "other"}),
            "other",
        )
        with self.assertRaises(plugin.client.PluginError):
            gates.resolve_collection(open_ledger, "paranoy_search_claims", {})


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        plugin.state.use_data_dir(Path(self.tmp.name))
        self._secret = plugin.signing.secret_configured
        plugin.signing.secret_configured = lambda: True

    def tearDown(self):
        plugin.signing.secret_configured = self._secret
        plugin.state.use_data_dir(None)
        self.tmp.cleanup()

    def test_visibility_follows_mode_and_role(self):
        reader = settings(mode="reader")
        self.assertTrue(gates.tool_visible(reader, "paranoy_search_claims"))
        self.assertFalse(gates.tool_visible(reader, "paranoy_add_claim"))
        self.assertFalse(gates.tool_visible(reader, "paranoy_set_admins"))
        # Without a pin, reads stay visible and take a per-call collection.
        self.assertTrue(gates.tool_visible(settings(mode="reader", collection=""), "paranoy_search_claims"))
        self.assertTrue(gates.tool_visible(settings(mode="reader", collection=""), "paranoy_list_collections"))

        miner = settings(mode="contributor", active_role="Claim Miner", roles_list=["Claim Miner"])
        self.assertFalse(gates.tool_visible(miner, "paranoy_add_evidence"))
        self.assertTrue(gates.tool_visible(miner, "paranoy_add_claim"))
        self.assertFalse(gates.tool_visible(miner, "paranoy_add_link"))

        ponder = settings(mode="contributor", active_role="Ponder", roles_list=["Ponder"])
        self.assertFalse(gates.tool_visible(ponder, "paranoy_add_evidence"))
        self.assertFalse(gates.tool_visible(ponder, "paranoy_add_claim"))
        self.assertTrue(gates.tool_visible(ponder, "paranoy_journal"))

        linker = settings(mode="contributor", active_role="Linker", roles_list=["Linker"])
        self.assertTrue(gates.tool_visible(linker, "paranoy_add_link"))
        self.assertFalse(gates.tool_visible(linker, "paranoy_add_claim"))

        self.assertFalse(gates.tool_visible(settings(mode="admin"), "paranoy_add_claim"))
        self.assertTrue(gates.tool_visible(settings(mode="admin"), "paranoy_set_admins"))
        self.assertFalse(gates.tool_visible(settings(mode="admin"), "paranoy_set_account_agents"))

        ledger = settings(mode="ledger", collection="")
        self.assertTrue(gates.tool_visible(ledger, "paranoy_add_claim"))
        self.assertTrue(gates.tool_visible(ledger, "paranoy_update_overview"))
        self.assertFalse(gates.tool_visible(ledger, "paranoy_set_admins"))
        self.assertFalse(gates.tool_visible(settings(key_configured=False), "paranoy_status"))

    def test_ledger_content_needs_an_explicit_ask(self):
        class Ctx:
            def get_config(self, key, default=None):
                values = {"mode": "ledger", "collection": "pump", "require_publish_approval": False}
                return values.get(key, default)

        directive = gates.pre_tool_directive(Ctx(), "paranoy_add_claim", {"text": "A claim."})
        self.assertEqual(directive["action"], "block")
        self.assertIn("owner_asked", directive["message"])

    def test_list_writes_always_require_approval(self):
        class Ctx:
            def __init__(self):
                self.cron = object()

            def get_config(self, key, default=None):
                values = {
                    "mode": "admin",
                    "collection": "pump",
                    "require_publish_approval": False,
                    "allow_unattended_writes": True,
                }
                return values.get(key, default)

            def current_cron_execution(self):
                return self.cron

        directive = gates.pre_tool_directive(
            Ctx(), "paranoy_add_members", {"add": ["abc"]}
        )
        self.assertEqual(directive["action"], "approve")

    def test_unattended_content_writes_fail_closed(self):
        class Ctx:
            def __init__(self, allowed):
                self.allowed = allowed

            def get_config(self, key, default=None):
                values = {
                    "mode": "contributor",
                    "collection": "pump",
                    "roles": "Scout",
                    "require_publish_approval": True,
                    "allow_unattended_writes": self.allowed,
                }
                return values.get(key, default)

            def current_cron_execution(self):
                return object()

        blocked = gates.pre_tool_directive(Ctx(False), "paranoy_add_evidence", {"text": "full"})
        self.assertEqual(blocked["action"], "block")
        allowed = gates.pre_tool_directive(Ctx(True), "paranoy_add_evidence", {"text": "full"})
        self.assertIsNone(allowed)


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        plugin.state.use_data_dir(Path(self.tmp.name))

    def tearDown(self):
        plugin.state.use_data_dir(None)
        self.tmp.cleanup()

    def test_register_loads_every_tool(self):
        class Ctx:
            def __init__(self):
                self.tools = []
                self.hooks = []
                self.skills = []
                self.commands = []
                self.settings = {}

            def register_skill(self, name, path, description=""):
                self.assert_file = Path(path).is_file()
                self.skills.append(name)

            def register_command(self, name, handler, description="", args_hint=""):
                self.commands.append(name)
                self.slash = handler

            def register_cli_command(self, **kwargs):
                self.cli = kwargs["handler_fn"]

            def register_hook(self, name, callback, **kwargs):
                self.hooks.append((name, callback))

            def register_tool(self, **kwargs):
                self.tools.append(kwargs["name"])

            def get_config(self, key, default=None):
                return self.settings.get(key, default)

            def set_config(self, key, value):
                self.settings[key] = value

        ctx = Ctx()
        plugin.register(ctx)
        self.assertEqual(ctx.tools, list(schemas.ALL_TOOL_NAMES))
        self.assertEqual([name for name, _ in ctx.hooks], list(schemas.HOOKS))
        self.assertEqual(ctx.skills, ["reader", "contributor", "admin", "ledger", "setup", "cadence"])
        self.assertIn("paranoy", ctx.commands)
        who = ctx.slash("whoami")
        self.assertNotIn("nsec1", who)
        context = ctx.hooks[0][1](user_message="hello")
        self.assertIn("context", context)
        self.assertIn("retrieval aids", context["context"])
        self.assertNotIn("nsec1", context["context"])
        ctx.slash("mode reader")
        self.assertEqual(ctx.settings["mode"], "reader")


class ManifestTests(unittest.TestCase):
    def test_registered_names_match_the_manifest(self):
        text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text()
        listed = []
        capturing = False
        for line in text.splitlines():
            if line.startswith("provides_tools:"):
                capturing = True
                continue
            if capturing:
                if line.startswith("  - "):
                    listed.append(line.split("- ", 1)[1].strip())
                else:
                    break
        self.assertEqual(listed, list(schemas.ALL_TOOL_NAMES))
        self.assertNotIn("publish", listed)
        self.assertTrue(all(name.startswith("paranoy_") for name in listed))


if __name__ == "__main__":
    unittest.main()

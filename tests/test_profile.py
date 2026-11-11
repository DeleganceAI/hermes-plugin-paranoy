import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeService, load_plugin

plugin = load_plugin()
gates = plugin.gates
schemas = plugin.schemas
runtime = plugin.runtime
PluginError = plugin.client.PluginError
SECRET = "11" * 32


class Ctx:
    """Minimal Hermes context: config, registration, and an optional cron run."""

    def __init__(self, cron=False, **settings):
        self.settings = dict(settings)
        self.cron = cron
        self.tools = {}
        self.skills = {}
        self.hooks = {}
        self.middleware = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value

    def current_cron_execution(self):
        return object() if self.cron else None

    def register_skill(self, name, path, description=""):
        self.skills[name] = (Path(path), description)

    def register_command(self, name, handler, description="", args_hint=""):
        self.slash = handler
        self.args_hint = args_hint

    def register_cli_command(self, **kwargs):
        self.cli_setup = kwargs["setup_fn"]

    def register_hook(self, name, callback, **kwargs):
        self.hooks[name] = callback

    def register_middleware(self, kind, callback, **kwargs):
        self.middleware[kind] = callback

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs


class RefusingService(FakeService):
    def __init__(self, plugin_module, message):
        super().__init__(plugin_module)
        self.message = message

    def publish(self, events):
        self.published.append(events)
        raise PluginError(self.message)


class KeyedTestCase(unittest.TestCase):
    """Pretend Hermes holds a key, and keep state in a temporary directory."""

    has_key = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        plugin.state.use_data_dir(Path(self.tmp.name))
        self._configured = plugin.signing.secret_configured
        self._load = plugin.signing.load_secret_hex
        if self.has_key:
            plugin.signing.secret_configured = lambda: True
            plugin.signing.load_secret_hex = lambda: SECRET
        else:
            plugin.signing.secret_configured = lambda: False

            def missing():
                raise plugin.signing.SecretError("no key")

            plugin.signing.load_secret_hex = missing

    def tearDown(self):
        plugin.signing.secret_configured = self._configured
        plugin.signing.load_secret_hex = self._load
        plugin.state.use_data_dir(None)
        self.tmp.cleanup()

    def kind0(self, events):
        found = [event for event in events if event["kind"] == 0]
        self.assertEqual(len(found), 1)
        return json.loads(found[0]["content"])


class ProfileHelperTests(KeyedTestCase):
    def test_defaults_live_in_one_place(self):
        self.assertEqual(
            runtime.profile_content({}),
            {"name": "paranoy-agent", "about": "agent"},
        )
        self.assertEqual(
            runtime.profile_content({"agent_name": " scout ", "agent_about": "agent: reads"}),
            {"name": "scout", "about": "agent: reads"},
        )

    def test_preface_uses_the_shared_profile(self):
        service = FakeService(plugin)
        batch = runtime.preface(service, {"auto_sign_terms": True}, [])
        self.assertEqual([event["kind"] for event in batch], [4706, 0])
        self.assertEqual(self.kind0(batch), {"name": "paranoy-agent", "about": "agent"})

    def test_publish_profile_sends_one_kind_0(self):
        service = FakeService(plugin)
        result = runtime.publish_profile(service, {"agent_name": "scout", "agent_about": "agent: x"})
        self.assertEqual(len(service.published), 1)
        self.assertEqual([event["kind"] for event in service.published[0]], [0])
        self.assertEqual(self.kind0(service.published[0]), {"name": "scout", "about": "agent: x"})
        self.assertEqual(result["accepted"], [service.published[0][0]["id"]])
        self.assertTrue(plugin.state.load_state().get("profile_published_at"))

    def test_empty_and_secret_values_are_rejected(self):
        with self.assertRaises(PluginError):
            gates.profile_value("name", "   ")
        with self.assertRaises(PluginError):
            gates.profile_value("name", "a\nb")
        with self.assertRaises(PluginError):
            gates.profile_value("name", "x" * (schemas.PROFILE_NAME_MAX + 1))
        with self.assertRaises(PluginError):
            gates.profile_value("about", "nsec1abc")
        self.assertEqual(gates.profile_value("name", " scout "), "scout")


class SlashProfileTests(KeyedTestCase):
    def test_show_set_and_publish(self):
        ctx = Ctx(mode="reader")
        service = FakeService(plugin)
        shown = gates.slash_result(ctx, "profile")
        self.assertIn("name: paranoy-agent (default", shown)
        self.assertIn("about: agent (default", shown)
        self.assertIn("npub: npub1", shown)

        self.assertIn("Saved", gates.slash_result(ctx, "profile name pump scout"))
        self.assertEqual(ctx.settings["agent_name"], "pump scout")
        gates.slash_result(ctx, "profile about agent: finds pump reports")
        self.assertEqual(ctx.settings["agent_about"], "agent: finds pump reports")
        self.assertEqual(gates.slash_result(ctx, "profile name"), "name: pump scout")

        out = gates.slash_result(ctx, "profile publish", lambda settings: service)
        self.assertIn("Published profile", out)
        self.assertEqual(len(service.published), 1)
        self.assertEqual(len(service.published[0]), 1)
        self.assertEqual(
            self.kind0(service.published[0]),
            {"name": "pump scout", "about": "agent: finds pump reports"},
        )

    def test_secret_and_empty_values_are_not_saved(self):
        ctx = Ctx()
        self.assertEqual(gates.slash_result(ctx, "profile name nsec1qqqq"), "That value was not saved.")
        self.assertNotIn("agent_name", ctx.settings)
        self.assertIn("Usage", gates.slash_result(ctx, "profile rename x"))

    def test_publish_refusal_names_the_fix(self):
        ctx = Ctx(agent_name="scout")
        service = RefusingService(plugin, "abc has not signed up and is not a member of any collection")
        out = gates.slash_result(ctx, "profile publish", lambda settings: service)
        self.assertIn("Your agents", out)
        self.assertIn("https://paranoy.org/connect", out)
        self.assertIn("npub1", out)
        self.assertEqual(ctx.settings["agent_name"], "scout")


class ProfileToolTests(KeyedTestCase):
    def register(self, **settings):
        ctx = Ctx(**settings)
        service = FakeService(plugin)
        plugin.register(ctx)
        return ctx, service

    def test_reader_with_no_collection_sees_the_tool(self):
        reader = gates.read_settings(Ctx(mode="reader"))
        self.assertEqual(reader["collection"], "")
        self.assertTrue(reader["key_configured"])
        self.assertTrue(gates.tool_visible(reader, "paranoy_set_profile"))
        self.assertTrue(gates.tool_visible(reader, "paranoy_status"))
        self.assertTrue(gates.tool_visible(reader, "paranoy_search_claims"))
        for mode in schemas.MODES:
            self.assertTrue(gates.tool_visible({"mode": mode, "key_configured": True}, "paranoy_set_profile"))
        self.assertFalse(gates.tool_visible({"mode": "reader", "key_configured": False}, "paranoy_set_profile"))
        directive = gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_set_profile", {"name": "scout"})
        self.assertEqual(directive["action"], "approve")

    def test_approval_and_cron_gates(self):
        quiet = Ctx(mode="contributor", collection="pump", require_publish_approval=False)
        self.assertIsNone(gates.pre_tool_directive(quiet, "paranoy_set_profile", {"name": "scout"}))
        cron = Ctx(cron=True, mode="admin")
        blocked = gates.pre_tool_directive(cron, "paranoy_set_profile", {"name": "scout"})
        self.assertEqual(blocked["action"], "block")
        self.assertIn("cron", blocked["message"])
        allowed = Ctx(cron=True, mode="admin", allow_unattended_writes=True)
        self.assertIsNone(gates.pre_tool_directive(allowed, "paranoy_set_profile", {"name": "scout"}))
        secret = gates.pre_tool_directive(Ctx(), "paranoy_set_profile", {"about": "nsec1abc"})
        self.assertEqual(secret["action"], "block")

    def test_tool_saves_and_publishes(self):
        tool = plugin.tools.profile
        settings = gates.read_settings(Ctx(agent_name="old", agent_about="agent: old"))
        saved = {}
        service = FakeService(plugin)
        result = tool.set_profile(service, settings, {"about": "agent: new"}, saved.__setitem__)
        self.assertEqual(saved, {"agent_about": "agent: new"})
        self.assertEqual(self.kind0(service.published[0]), {"name": "old", "about": "agent: new"})
        self.assertEqual(result["profile"], {"name": "old", "about": "agent: new"})
        with self.assertRaises(PluginError):
            tool.set_profile(service, settings, {"name": ""}, saved.__setitem__)
        self.assertEqual(len(service.published), 1)

    def test_tool_saves_even_when_the_key_is_unknown(self):
        tool = plugin.tools.profile
        settings = gates.read_settings(Ctx(mode="reader"))
        saved = {}
        service = RefusingService(plugin, "abc has not signed up and is not a member of any collection")
        result = tool.set_profile(service, settings, {"name": "scout", "about": "agent: x"}, saved.__setitem__)
        self.assertEqual(saved, {"agent_name": "scout", "agent_about": "agent: x"})
        self.assertIn("Your agents", result["error"])
        self.assertIn(service.npub, result["error"])
        self.assertIn("saved", result["error"])

    def test_registered_handler_uses_ctx_settings(self):
        ctx = Ctx(mode="reader")
        plugin.register(ctx)
        self.assertIn("paranoy_set_profile", ctx.tools)
        self.assertTrue(ctx.tools["paranoy_set_profile"]["check_fn"]())
        self.assertIn("profile", ctx.args_hint)


class SetupHintTests(KeyedTestCase):
    def context(self, **settings):
        return gates.prompt_context(gates.read_settings(Ctx(**settings)))

    def test_no_collection_in_reader_mode(self):
        text = self.context(mode="reader")
        self.assertIn("no collection is pinned", text)
        self.assertIn("npub1", text)
        self.assertIn("Your agents", text)
        self.assertIn("Where they work", text)
        self.assertIn("/paranoy collection <slug>", text)
        self.assertIn("paranoy_set_profile", text)
        self.assertIn("The mode does not need to change", text)

    def test_contributor_without_roles(self):
        text = self.context(mode="contributor", collection="pump")
        self.assertIn("/paranoy roles", text)
        self.assertNotIn("no collection is pinned", text)

    def test_complete_setup_adds_one_line(self):
        plugin.state.update_state(profile_published_at="2026-01-01T00:00:00Z")
        text = self.context(mode="reader", collection="pump", agent_name="scout")
        self.assertNotIn("Setup:", text)
        self.assertEqual(text.count("Setup looks complete"), 1)
        self.assertIn("paranoy_set_profile works in any mode", text)


class NoKeyHintTests(KeyedTestCase):
    has_key = False

    def test_no_key_points_to_keygen(self):
        text = gates.prompt_context(gates.read_settings(Ctx()))
        self.assertIn("hermes paranoy keygen", text)
        self.assertIn("You cannot do it", text)
        self.assertNotIn("nsec1", text)
        self.assertFalse(gates.tool_visible(gates.read_settings(Ctx()), "paranoy_set_profile"))


class SkillTests(unittest.TestCase):
    def test_setup_skill_is_registered_and_linked(self):
        ctx = Ctx()
        plugin.register(ctx)
        self.assertIn("setup", ctx.skills)
        path, description = ctx.skills["setup"]
        self.assertTrue(path.is_file())
        self.assertTrue(description)
        text = path.read_text()
        self.assertTrue(text.startswith("---\nname: Paranoy agent setup\n"))
        for phrase in ("Your agents", "Where they work", "paranoy_set_profile", "paranoy_status",
                       "/paranoy collection", "paranoy_set_active_role", "## Troubleshooting"):
            self.assertIn(phrase, text)
        self.assertNotIn("nsec1", text)
        for name in ("reader", "contributor", "admin", "ledger", "cadence"):
            body = ctx.skills[name][0].read_text()
            self.assertIn("If the agent is set up wrong", body)


if __name__ == "__main__":
    unittest.main()

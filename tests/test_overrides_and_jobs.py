"""Per-call collection targeting and cadence jobs."""

import json
import shlex
import unittest

from tests.support import FakeService, load_plugin
from tests.test_config_tools import listing
from tests.test_profile import Ctx, KeyedTestCase

plugin = load_plugin()
gates = plugin.gates
schemas = plugin.schemas
jobs = plugin.jobs
read = plugin.tools.read
write = plugin.tools.write
job_tool = plugin.tools.jobs
PluginError = plugin.client.PluginError


def service_with_listing():
    service = FakeService(plugin)
    service.responses["list_collections"] = listing(service)
    service.responses["search_claims"] = {"result": []}
    return service


class PerCallCollectionTests(KeyedTestCase):
    def test_read_targets_another_collection_without_moving_the_pin(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        service = service_with_listing()
        settings = gates.read_settings(ctx)
        read.search_claims(service, settings, {"collection": "ufo-uap-worldview", "text": "lights"})
        self.assertIn(("search_claims", {"collection": "ufo-uap-worldview", "text": "lights"}), service.calls)
        self.assertIn(("list_collections", {}), service.calls)
        self.assertEqual(ctx.settings["collection"], "super-collection")

    def test_read_of_the_pin_skips_the_membership_call(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        service = service_with_listing()
        read.search_claims(service, gates.read_settings(ctx), {"text": "x"})
        self.assertNotIn(("list_collections", {}), service.calls)
        self.assertEqual(service.calls[-1][1]["collection"], "super-collection")

    def test_non_member_target_is_refused(self):
        ctx = Ctx(mode="reader", collection="super-collection")
        service = service_with_listing()
        with self.assertRaises(PluginError) as caught:
            read.search_claims(service, gates.read_settings(ctx), {"collection": "not-mine", "text": "x"})
        self.assertIn("not a member of not-mine", str(caught.exception))
        self.assertFalse(any(name == "search_claims" for name, _ in service.calls))

    def test_reader_without_a_pin_can_pass_a_collection(self):
        ctx = Ctx(mode="reader")
        service = service_with_listing()
        out = json.loads(plugin.tools.read._wrap(read.search_claims, lambda s: service, lambda: gates.read_settings(ctx))(
            {"collection": "ufo-uap-worldview", "text": "x"}
        ))
        self.assertNotIn("error", out)
        out = json.loads(plugin.tools.read._wrap(read.search_claims, lambda s: service, lambda: gates.read_settings(ctx))(
            {"text": "x"}
        ))
        self.assertIn("No collection is pinned", out["error"])

    def test_write_targets_another_collection_and_checks_membership(self):
        ctx = Ctx(mode="contributor", collection="super-collection", roles="Claim Miner")
        service = service_with_listing()
        service.responses["list_claims"] = {"result": []}
        settings = gates.read_settings(ctx)
        self.assertEqual(
            write._collection(service, settings, "paranoy_add_claim", {"collection": "ufo-uap-worldview"}),
            "ufo-uap-worldview",
        )
        with self.assertRaises(PluginError):
            write._collection(service, settings, "paranoy_add_claim", {"collection": "not-mine"})
        self.assertEqual(ctx.settings["collection"], "super-collection")

    def test_override_is_allowed_in_cron_but_write_gating_stays(self):
        read_cron = Ctx(cron=True, mode="reader", collection="super-collection")
        self.assertIsNone(
            gates.pre_tool_directive(read_cron, "paranoy_search_claims", {"collection": "ufo-uap-worldview", "text": "x"})
        )
        write_cron = Ctx(cron=True, mode="contributor", collection="super-collection", roles="Scout")
        blocked = gates.pre_tool_directive(
            write_cron, "paranoy_add_evidence", {"collection": "ufo-uap-worldview", "text": "t"}
        )
        self.assertEqual(blocked["action"], "block")
        self.assertIn("cron", blocked["message"])
        allowed = Ctx(cron=True, mode="contributor", collection="super-collection", roles="Scout",
                      allow_unattended_writes=True)
        self.assertIsNone(
            gates.pre_tool_directive(allowed, "paranoy_add_evidence", {"collection": "ufo-uap-worldview", "text": "t"})
        )
        attended = Ctx(mode="contributor", collection="super-collection", roles="Scout")
        directive = gates.pre_tool_directive(
            attended, "paranoy_add_evidence", {"collection": "ufo-uap-worldview", "text": "t"}
        )
        self.assertEqual(directive["action"], "approve")

    def test_admin_override_stays_in_the_managed_set(self):
        admin = gates.read_settings(Ctx(mode="admin", collections="beta"))
        with self.assertRaises(PluginError):
            gates.resolve_collection(admin, "paranoy_search_claims", {"collection": "ufo-uap-worldview"})

    def test_role_null_rows_count_as_membership(self):
        service = service_with_listing()
        row = plugin.runtime.assert_membership(service, "ufo-uap-worldview", administer=False)
        self.assertEqual(row["slug"], "ufo-uap-worldview")
        with self.assertRaises(PluginError):
            plugin.runtime.assert_membership(service, "ufo-uap-worldview", administer=True)

    def test_scoped_schemas_say_the_pin_does_not_change(self):
        for name in ("paranoy_search_claims", "paranoy_list_evidence", "paranoy_add_evidence", "paranoy_add_link"):
            text = schemas.SCHEMAS[name]["parameters"]["properties"]["collection"]["description"]
            self.assertIn("this call only", text, name)


JOB = {
    "action": "create",
    "role": "scout",
    "collection": "ufo-uap-worldview",
    "target": 50,
    "cadence": "hourly",
    "focus": "new primary sources",
}


class JobTests(KeyedTestCase):
    def test_validation(self):
        self.assertEqual(jobs.check_cadence("hourly"), "every 1h")
        self.assertEqual(jobs.check_cadence("Every  2h"), "every 2h")
        self.assertEqual(jobs.check_cadence("0 * * * *"), "0 * * * *")
        for bad in ("", "sometimes", "every 0h", "* * *", "0 * * * * *"):
            with self.assertRaises(PluginError, msg=bad):
                jobs.check_cadence(bad)
        self.assertEqual(jobs.check_role("claim miner"), "Claim Miner")
        with self.assertRaises(PluginError):
            jobs.check_role("wizard")
        for bad in (0, -1, 501, "x", None):
            with self.assertRaises(PluginError, msg=str(bad)):
                jobs.check_target(bad)
        with self.assertRaises(PluginError):
            jobs.validate({**JOB, "collection": "Bad Slug"})
        with self.assertRaises(PluginError):
            jobs.validate({**JOB, "focus": "nsec1abc"})

    def test_create_builds_the_hermes_job(self):
        ctx = Ctx(mode="contributor", collection="super-collection", roles="Scout")
        service = service_with_listing()
        result = job_tool.schedule_job(service, gates.read_settings(ctx), dict(JOB))
        spec = result["job"]
        self.assertEqual(spec["name"], "paranoy-scout-ufo-uap-worldview")
        cron = result["cronjob_manage"]
        self.assertEqual(cron["action"], "create")
        self.assertEqual(cron["schedule"], "every 1h")
        self.assertEqual(cron["skills"], ["paranoy:contributor"])
        prompt = cron["prompt"]
        self.assertIn('Pass collection "ufo-uap-worldview" on every collection-scoped', prompt)
        self.assertIn("Do not call paranoy_set_collection", prompt)
        self.assertIn("Do only Scout work", prompt)
        self.assertIn("Dedupe before every write", prompt)
        self.assertIn("Stop after 50 new evidence documents", prompt)
        self.assertIn("new primary sources", prompt)
        self.assertEqual(shlex.split(result["cli_command"])[:4], ["hermes", "cron", "create", "every 1h"])
        self.assertIn(prompt, shlex.split(result["cli_command"]))
        self.assertEqual(result["volume"]["max_items_per_day"], 1200)
        self.assertTrue(any("allow_unattended_writes" in note for note in result["prerequisites"]))
        self.assertEqual(ctx.settings["collection"], "super-collection")
        self.assertEqual(service.published, [])
        listed = jobs.list_jobs()["jobs"]
        self.assertEqual([job["name"] for job in listed], ["paranoy-scout-ufo-uap-worldview"])

    def test_prerequisites_follow_settings(self):
        ready = gates.read_settings(Ctx(mode="contributor", roles="Scout", allow_unattended_writes=True))
        self.assertEqual(jobs.prerequisites(ready, jobs.validate(JOB)), [])
        reader = gates.read_settings(Ctx(mode="reader"))
        notes = jobs.prerequisites(reader, jobs.validate(JOB))
        self.assertEqual(len(notes), 3)
        self.assertIn("paranoy_set_mode", notes[0])

    def test_create_refuses_a_non_member_collection(self):
        service = service_with_listing()
        result = job_tool.schedule_job(service, gates.read_settings(Ctx()), {**JOB, "collection": "not-mine"})
        self.assertIn("not a member", result["error"])
        self.assertIn("ufo-uap-worldview", result["available"])
        self.assertEqual(jobs.list_jobs()["jobs"], [])

    def test_remove(self):
        service = service_with_listing()
        job_tool.schedule_job(service, gates.read_settings(Ctx()), dict(JOB))
        removed = job_tool.schedule_job(service, gates.read_settings(Ctx()), {"action": "remove", "name": "paranoy-scout-ufo-uap-worldview"})
        self.assertIn("cronjob_manage", removed["next_step"])
        self.assertEqual(jobs.list_jobs()["jobs"], [])
        with self.assertRaises(PluginError):
            jobs.remove_job({"name": "paranoy-scout-ufo-uap-worldview"})

    def test_approval_and_cron_refusal(self):
        directive = gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_schedule_job", dict(JOB))
        self.assertEqual(directive["action"], "approve")
        self.assertIsNone(gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_schedule_job", {"action": "list"}))
        for cron in (Ctx(cron=True), Ctx(cron=True, allow_unattended_writes=True, require_publish_approval=False)):
            blocked = gates.pre_tool_directive(cron, "paranoy_schedule_job", dict(JOB))
            self.assertEqual(blocked["action"], "block")
            blocked = gates.pre_tool_directive(cron, "paranoy_schedule_job", {"action": "remove", "name": "x"})
            self.assertEqual(blocked["action"], "block")
        self.assertIsNone(gates.pre_tool_directive(Ctx(cron=True), "paranoy_schedule_job", {"action": "list"}))
        bad = gates.pre_tool_directive(Ctx(), "paranoy_schedule_job", {**JOB, "cadence": "sometimes"})
        self.assertEqual(bad["action"], "block")
        bad = gates.pre_tool_directive(Ctx(), "paranoy_schedule_job", {"action": "explode"})
        self.assertEqual(bad["action"], "block")
        self.assertNotIn("paranoy_schedule_job", schemas.APPROVAL_WRITES)

    def test_visible_in_every_mode_with_a_key(self):
        for mode in schemas.MODES:
            self.assertTrue(gates.tool_visible({"mode": mode, "key_configured": True}, "paranoy_schedule_job"))
        self.assertFalse(gates.tool_visible({"mode": "reader", "key_configured": False}, "paranoy_schedule_job"))

    def test_readme_example_volume(self):
        first = jobs.validate(JOB)
        second = jobs.validate({**JOB, "collection": "super-collection", "focus": "fodmap"})
        total = sum(jobs.runs_per_day(spec["cadence"]) * spec["target"] for spec in (first, second))
        self.assertEqual(total, 2400)
        self.assertNotEqual(first["name"], second["name"])


class SlashJobTests(KeyedTestCase):
    def test_slash_create_list_remove_without_dispatch(self):
        ctx = Ctx(mode="contributor", roles="Scout")
        service = service_with_listing()
        out = gates.slash_result(
            ctx,
            'job create --role scout --collection ufo-uap-worldview --target 50 --cadence hourly --focus "new sources"',
            lambda settings: service,
        )
        self.assertIn("Built paranoy-scout-ufo-uap-worldview", out)
        self.assertIn("hermes cron create", out)
        self.assertIn("allow_unattended_writes", out)
        self.assertIn("paranoy-scout-ufo-uap-worldview", gates.slash_result(ctx, "job list"))
        out = gates.slash_result(ctx, "job remove paranoy-scout-ufo-uap-worldview")
        self.assertIn("Removed", out)
        self.assertIn("No Paranoy jobs", gates.slash_result(ctx, "job list"))
        self.assertIn("Usage", gates.slash_result(ctx, "job"))
        self.assertIn("Usage", gates.slash_result(ctx, "job create --role"))
        self.assertIn("cadence", gates.slash_result(ctx, "job create --role scout --collection x --target 5 --cadence nope"))

    def test_slash_create_uses_dispatch_tool_when_present(self):
        ctx = Ctx(mode="contributor", roles="Scout")
        sent = []

        def dispatch(name, arguments):
            sent.append((name, arguments))
            if arguments["action"] == "create":
                return json.dumps({"success": True, "job_id": "abc123"})
            return json.dumps({"success": True})

        ctx.dispatch_tool = dispatch
        service = service_with_listing()
        out = gates.slash_result(
            ctx,
            "job create --role Scout --collection super-collection --target 50 --cadence hourly --focus fodmap",
            lambda settings: service,
        )
        self.assertIn("Scheduled in Hermes as job abc123", out)
        self.assertEqual(sent[0][0], "cronjob_manage")
        self.assertEqual(sent[0][1]["schedule"], "every 1h")
        self.assertEqual(jobs.list_jobs()["jobs"][0]["hermes_job_id"], "abc123")
        out = gates.slash_result(ctx, "job remove paranoy-scout-super-collection")
        self.assertIn("Hermes job abc123", out)
        self.assertEqual(sent[-1], ("cronjob_manage", {"action": "remove", "job_id": "abc123"}))


class JobParityTests(unittest.TestCase):
    def test_job_subcommands_map_to_the_tool(self):
        for sub in gates.JOB_SUBCOMMANDS:
            key = ("job " + sub).strip()
            self.assertEqual(schemas.SLASH_TOOLS[key], "paranoy_schedule_job")


if __name__ == "__main__":
    unittest.main()

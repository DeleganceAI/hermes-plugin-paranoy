import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeService, load_plugin

plugin = load_plugin()
write = plugin.tools.write
governance = plugin.tools.governance


def member_settings(**extra):
    base = {
        "mode": "contributor",
        "collection": "pump",
        "managed": [],
        "roles_list": ["Scout"],
        "active_role": "Scout",
        "auto_sign_terms": True,
        "terms_version": "1",
        "terms_sha256": plugin.schemas.TERMS_SHA256,
        "agent_name": "scout",
        "agent_about": "agent",
        "key_configured": True,
    }
    base.update(extra)
    return base


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        plugin.state.use_data_dir(Path(self.tmp.name))
        plugin.state.update_state(terms_published=True)
        self.service = FakeService(
            plugin,
            {
                "list_collections": {
                    "result": [
                        {"slug": "pump", "role": "member", "owner": "owner", "admins": ["admin-a"]}
                    ]
                },
                "list_evidence": {"result": []},
                "search_claims": {"items": []},
            },
        )

    def tearDown(self):
        plugin.state.use_data_dir(None)
        self.tmp.cleanup()

    def test_rebuttal_is_a_gate_and_is_not_stored(self):
        with self.assertRaises(plugin.client.PluginError):
            write.add_claim(
                self.service,
                member_settings(),
                {"text": "The pump used less electricity.", "would_be_rebutted_by": "  "},
            )
        self.assertEqual(self.service.published, [])
        result = write.add_claim(
            self.service,
            member_settings(),
            {
                "text": "The pump used less electricity.",
                "would_be_rebutted_by": "A longer test showing the opposite.",
            },
        )
        self.assertIn(4702, result["stored_kinds"])
        content = json.loads(self.service.published[-1][-1]["content"])
        self.assertEqual(set(content), {"collection", "text"})
        self.assertNotIn("rebut", json.dumps(content).lower())

    def test_quote_mismatch_does_not_publish(self):
        evidence = "During the two-hour test, the new pump used 18 Wh."
        self.service.responses["get_event"] = lambda args: {
            "result": {
                "id": args["id"],
                "kind": 4701 if args["id"] == "ev1" else 4702,
                "chars": len(evidence),
                "content": {"text": evidence},
            }
        }
        with self.assertRaises(plugin.client.PluginError):
            write.add_link(
                self.service,
                member_settings(active_role="Linker", roles_list=["Linker"]),
                {
                    "from": "ev1",
                    "to": "cl1",
                    "relation": "SUPPORTS",
                    "quote": "the new pump used less electricity",
                    "reason": "That is a paraphrase.",
                },
            )
        self.assertEqual(self.service.published, [])
        published = write.add_link(
            self.service,
            member_settings(active_role="Linker", roles_list=["Linker"]),
            {
                "from": "ev1",
                "to": "cl1",
                "relation": "SUPPORTS",
                "quote": "the new   pump USED 18 wh",
                "reason": "The report records that use.",
            },
        )
        self.assertEqual(published["stored_kind"], 4703)
        content = json.loads(self.service.published[-1][0]["content"])
        self.assertEqual(content["relation"], "SUPPORTS")
        self.assertIn("18", content["quote"])

    def test_gap_finder_rejects_support_links(self):
        with self.assertRaises(plugin.client.PluginError):
            write.add_link(
                self.service,
                member_settings(active_role="Gap Finder", roles_list=["Gap Finder"]),
                {
                    "from": "ev1",
                    "to": "cl1",
                    "relation": "SUPPORTS",
                    "quote": "anything",
                    "reason": "support",
                },
            )
        self.assertEqual(self.service.published, [])

    def test_fetch_merge_preview_handlers(self):
        admin = member_settings(mode="admin", collection="pump", managed=["pump"], active_role="")
        self.service.responses["list_members"] = {
            "role": "owner",
            "can_manage_members": True,
            "can_manage_admins": True,
            "members": [
                {"pubkey": "owner", "role": "owner"},
                {"pubkey": "member", "role": "member"},
            ],
        }
        self.service.responses["list_collections"] = {
            "result": [
                {
                    "slug": "pump",
                    "role": "owner",
                    "owner": "owner",
                    "admins": ["admin-a"],
                }
            ]
        }
        with self.assertRaises(plugin.client.PluginError):
            governance.remove_members(self.service, admin, {"remove": ["owner"]})
        self.assertEqual(self.service.published, [])
        with self.assertRaises(plugin.client.PluginError):
            governance.remove_members(self.service, admin, {"remove": ["member"]})
        self.assertEqual(self.service.published, [])
        wiped = governance.remove_members(
            self.service,
            admin,
            {"remove": ["member"], "confirm_owner_only": True},
        )
        members = json.loads(self.service.published[-1][-1]["content"])["members"]
        self.assertEqual(members, ["owner"])
        self.assertEqual(wiped["preview"]["removed"], ["member"])

        governance.set_admins(self.service, admin, {"add": ["owner", "admin-b"]})
        admins = json.loads(self.service.published[-1][-1]["content"])["admins"]
        self.assertNotIn("owner", admins)
        self.assertEqual(admins, ["admin-a", "admin-b"])

        with self.assertRaises(plugin.client.PluginError):
            governance.set_account_agents(
                self.service,
                member_settings(mode="ledger"),
                {"add": ["agent-b"]},
            )
        self.service.fetch_account = lambda: {"ledger": "keep-me", "agents": ["agent-a"]}
        with self.assertRaises(plugin.client.PluginError):
            governance.set_account_agents(
                self.service,
                member_settings(mode="ledger"),
                {"add": ["agent-b"], "ledger": "changed"},
            )
        published = governance.set_account_agents(
            self.service,
            member_settings(mode="ledger"),
            {"add": ["agent-b"]},
        )
        content = json.loads(self.service.published[-1][-1]["content"])
        self.assertEqual(content["ledger"], "keep-me")
        self.assertEqual(content["agents"], ["agent-a", "agent-b"])
        self.assertTrue(published["preview"]["ledger_unchanged"])


if __name__ == "__main__":
    unittest.main()

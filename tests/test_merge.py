import unittest

from tests.support import load_plugin

plugin = load_plugin()
merge = plugin.merge


class MergeTests(unittest.TestCase):
    def test_fetch_failure_refuses_the_write(self):
        for call in (
            lambda: merge.merge_members(None, add=["a"]),
            lambda: merge.merge_admins(None, "owner", add=["a"]),
            lambda: merge.merge_account_agents(None, add=["a"]),
            lambda: merge.merge_collection_agents(None, add=["a"]),
        ):
            with self.assertRaises(merge.MergeError):
                call()

    def test_owner_cannot_be_removed_and_a_wipe_needs_confirm(self):
        current = [
            {"pubkey": "owner", "role": "owner"},
            {"pubkey": "member", "role": "member"},
        ]
        with self.assertRaises(merge.MergeError):
            merge.merge_members(current, remove=["owner"])
        with self.assertRaises(merge.MergeError):
            merge.merge_members(current, remove=["member"])
        planned = merge.merge_members(current, remove=["member"], confirm_owner_only=True)
        self.assertEqual(planned["members"], ["owner"])
        added = merge.merge_members(current, add=["next"])
        self.assertEqual(added["members"], ["owner", "member", "next"])
        self.assertEqual(added["added"], ["next"])

    def test_owner_is_left_out_of_admins(self):
        planned = merge.merge_admins(
            ["admin-a"],
            "owner",
            add=["owner", "admin-b"],
            remove=[],
        )
        self.assertNotIn("owner", planned["admins"])
        self.assertEqual(planned["admins"], ["admin-a", "admin-b"])
        self.assertEqual(planned["owner_excluded"], "owner")

    def test_account_ledger_field_stays_the_fetched_value(self):
        planned = merge.merge_account_agents(
            {"ledger": "keep-me", "agents": ["agent-a"]},
            add=["agent-b"],
            remove=[],
        )
        self.assertEqual(planned["ledger"], "keep-me")
        self.assertEqual(planned["agents"], ["agent-a", "agent-b"])


if __name__ == "__main__":
    unittest.main()

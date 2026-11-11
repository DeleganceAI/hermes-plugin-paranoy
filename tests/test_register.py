import json
import tempfile
import unittest
from pathlib import Path

from tests.support import load_plugin

plugin = load_plugin()
runtime = plugin.runtime
state = plugin.state
signing = plugin.signing
ParanoyClient = plugin.client.ParanoyClient
PluginError = plugin.client.PluginError
SECRET = "11" * 32


class RegisterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        state.use_data_dir(Path(self.tmp.name))

    def tearDown(self):
        state.use_data_dir(None)
        self.tmp.cleanup()

    def client(self, answer):
        self.posts = []

        def poster(url, payload):
            self.posts.append((url, payload))
            return answer

        return ParanoyClient("https://paranoy.test/", SECRET, poster=poster)

    def test_email_registration_posts_signed_request_with_terms_and_profile(self):
        service = self.client({"registered": True, "npub": "npub1x"})
        text = runtime.register_account(service, {"agent_name": "scout"}, " owner@example.org ")
        url, payload = self.posts[0]
        self.assertEqual(url, "https://paranoy.test/hermes")
        request = payload["event"]
        self.assertTrue(signing.verify_event(request))
        self.assertEqual(request["kind"], 4707)
        body = json.loads(request["content"])
        self.assertEqual((body["tool"], body["args"]), ("hermes", {"email": "owner@example.org"}))
        self.assertEqual([e["kind"] for e in payload["events"]], [4706, 0])
        self.assertEqual(json.loads(payload["events"][1]["content"])["name"], "scout")
        self.assertTrue(all(e["pubkey"] == request["pubkey"] for e in payload["events"]))
        self.assertTrue(state.load_state().get("terms_published"))
        self.assertIn("owner@example.org holds the account", text)
        self.assertIn("/paranoy mode ledger", text)

    def test_without_email_the_agent_is_its_own_account(self):
        service = self.client({"registered": False})
        text = runtime.register_account(service, {}, None)
        self.assertEqual(json.loads(self.posts[0][1]["event"]["content"])["args"], {})
        self.assertTrue(text.startswith("Already registered"))

    def test_server_refusal_is_a_plugin_error_and_marks_nothing(self):
        service = self.client({"error": "an account already exists for this address"})
        with self.assertRaises(PluginError) as caught:
            runtime.register_account(service, {}, "owner@example.org")
        self.assertIn("already exists", str(caught.exception))
        self.assertFalse(state.load_state().get("terms_published"))


if __name__ == "__main__":
    unittest.main()

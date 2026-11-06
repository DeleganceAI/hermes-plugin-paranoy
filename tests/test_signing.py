import json
import secrets
import unittest

from tests.support import load_plugin

plugin = load_plugin()


class SigningTests(unittest.TestCase):
    def test_event_id_and_signature_verify(self):
        secret = secrets.token_bytes(32).hex()
        signer = plugin.signing.Signer(
            secret, clock=lambda: 1_700_000_000, aux_rand=lambda: b"\x33" * 32
        )
        event = signer.sign_event(4702, {"collection": "pump", "text": "A claim."})
        self.assertTrue(plugin.signing.verify_event(event))
        self.assertNotIn(secret, json.dumps(event))
        self.assertEqual(event["pubkey"], signer.pubkey)
        self.assertTrue(event["id"])
        self.assertEqual(len(event["sig"]), 128)

    def test_replay_nonce_changes_the_id(self):
        signer = plugin.signing.Signer("22" * 32, clock=lambda: 1_700_000_000)
        first = signer.sign_request("search_claims", {"collection": "pump", "text": "pump"}, nonce="aa", created_at=1_700_000_000)
        second = signer.sign_request("search_claims", {"collection": "pump", "text": "pump"}, nonce="bb", created_at=1_700_000_000)
        replay = signer.sign_request("search_claims", {"collection": "pump", "text": "pump"}, nonce="aa", created_at=1_700_000_000)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["id"], replay["id"])
        body = json.loads(first["content"])
        self.assertEqual(body["tool"], "search_claims")
        self.assertEqual(body["nonce"], "aa")
        self.assertEqual(body["args"]["collection"], "pump")
        self.assertTrue(plugin.signing.verify_event(first))
        self.assertTrue(plugin.signing.verify_event(second))

    def test_client_posts_a_signed_request_without_the_secret(self):
        secret = "33" * 32
        seen = []

        def poster(url, payload):
            seen.append((url, payload))
            return {"result": {"structuredContent": {"ok": True}}}

        client = plugin.client.ParanoyClient(
            "https://paranoy.example",
            secret,
            poster=poster,
            clock=lambda: 1_700_000_000,
        )
        first = client.call("list_collections", {})
        second = client.call("list_collections", {})
        self.assertEqual(first, {"ok": True})
        self.assertEqual(seen[0][0], "https://paranoy.example/mcp")
        request = seen[0][1]["params"]["arguments"]["request"]
        self.assertEqual(request["kind"], 4707)
        self.assertEqual(list(seen[0][1]["params"]["arguments"]), ["request"])
        content = json.loads(request["content"])
        self.assertEqual(content["tool"], "list_collections")
        self.assertTrue(content["nonce"])
        self.assertNotEqual(
            content["nonce"],
            json.loads(seen[1][1]["params"]["arguments"]["request"]["content"])["nonce"],
        )
        encoded = json.dumps(seen)
        self.assertNotIn(secret, encoded)
        self.assertTrue(plugin.signing.verify_event(request))

    def test_nsec_and_hex_load_the_same_key(self):
        raw = secrets.token_bytes(32)
        encoded = plugin.signing._bech32_encode("nsec", raw)
        self.assertEqual(plugin.signing.secret_to_hex(encoded), raw.hex())
        self.assertEqual(plugin.signing.secret_to_hex(raw.hex()), raw.hex())
        npub = plugin.signing.encode_npub(bytes.fromhex(plugin.signing.Signer(raw.hex()).pubkey))
        self.assertTrue(npub.startswith("npub1"))

    def test_keygen_prints_npub_only(self):
        stored = {}

        def save(key, value):
            stored["key"] = key
            stored["value"] = value

        text = plugin.signing.generate_and_store(save)
        self.assertEqual(stored["key"], plugin.schemas.SECRET_ENV)
        self.assertNotIn(stored["value"], text)
        self.assertNotIn("nsec1", text)
        self.assertIn("npub1", text)

        failed = plugin.signing.generate_and_store(self._boom)
        self.assertIn("could not store", failed)
        self.assertNotIn("nsec1", failed)

    @staticmethod
    def _boom(key, value):
        raise RuntimeError("store failed")


if __name__ == "__main__":
    unittest.main()

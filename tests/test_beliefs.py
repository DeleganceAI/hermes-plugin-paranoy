"""Belief scans stay local until a person approves one item."""

import json
import logging
import unittest

from tests.support import FakeService, load_plugin
from tests.test_profile import Ctx, KeyedTestCase

plugin = load_plugin()
beliefs = plugin.beliefs
gates = plugin.gates
schemas = plugin.schemas
PluginError = plugin.client.PluginError

PUMP = "I think the new pump uses less electricity than the old pump."
CLAIM = "The new pump uses less electricity than the old pump"


class RuleTests(unittest.TestCase):
    def test_present_tense_world_claims_pass(self):
        self.assertEqual(beliefs.claim_problems(CLAIM + "."), [])

    def test_form_rules_reject_without_judging_truth(self):
        samples = {
            "The new pump can use less electricity than the old pump.": "can or may",
            "The new pump may use less electricity than the old pump.": "can or may",
            "The new pump should use less electricity than the old pump.": "normative",
            "The new pump will use less electricity than the old pump.": "future",
            "The new pump used less electricity than the old pump.": "present tense",
            "Does the new pump use less electricity than the old pump?": "question",
            "I want the new pump to use less electricity than the old one.": "intention",
            "Hello there friend today.": "not a claim",
        }
        for text, needle in samples.items():
            problems = " ".join(beliefs.claim_problems(text))
            self.assertIn(needle, problems, text)
            self.assertNotIn("true", problems)
            self.assertNotIn("plausible", problems)


class ScanTests(KeyedTestCase):
    def tearDown(self):
        beliefs.use_history_reader(None)
        super().tearDown()

    def test_scan_does_not_read_until_consent(self):
        def reader(session_id):
            raise AssertionError(session_id)

        beliefs.use_history_reader(reader)
        beliefs.remember_turn(
            {"session_id": "s1", "user_message": PUMP, "conversation_history": [{"role": "user", "content": PUMP}]}
        )
        self.assertNotIn("pump", json.dumps(plugin.state.load_state()))
        with self.assertRaises(PluginError) as caught:
            beliefs.scan(session_id="s1")
        self.assertIn("Consent", str(caught.exception))

    def test_session_scan_lists_excerpts_and_a_neutral_candidate(self):
        seen = []

        def reader(session_id):
            seen.append(session_id)
            return [
                {"role": "assistant", "content": "The pump is obviously efficient."},
                {"role": "user", "content": PUMP},
                {"role": "user", "content": "Thanks."},
            ]

        beliefs.use_history_reader(reader)
        beliefs.record_consent(True)
        payload = beliefs.scan(session_id="sess-1")
        self.assertEqual(seen, ["sess-1"])
        self.assertEqual(
            [excerpt["text"] for excerpt in payload["excerpts"]],
            [PUMP, "Thanks."],
        )
        self.assertEqual(payload["excerpts"][0]["source"], "session")
        self.assertEqual(payload["candidates"][0]["claim"], CLAIM + ".")
        self.assertEqual(payload["candidates"][0]["quote"], PUMP)
        self.assertEqual(payload["candidates"][0]["assumptions"], [])
        self.assertFalse(payload["sent"])
        self.assertIn("Truth, tone, and plausibility are not judged", payload["note"])
        stored = (plugin.state.data_dir() / "state.json").read_text()
        self.assertIn(PUMP, stored)

    def test_pasted_transcript_keeps_user_lines_and_redacts_a_secret(self):
        beliefs.record_consent(True)
        secret = "nsec1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqs8k4y3"
        text = "\n".join(
            [
                "User: " + PUMP,
                "Assistant: maybe.",
                "User: the secret is " + secret,
            ]
        )
        payload = beliefs.scan(text)
        joined = json.dumps(payload)
        self.assertEqual(len(payload["excerpts"]), 2)
        self.assertNotIn(secret, joined)
        self.assertNotIn("maybe", joined)
        self.assertIn("[redacted]", payload["excerpts"][1]["text"])
        self.assertEqual(payload["excerpts"][0]["source"], "pasted")

    def test_hook_cache_is_the_fallback_and_is_not_logged(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            beliefs.record_consent(True)
            beliefs.remember_turn(
                {
                    "session_id": "s2",
                    "conversation_history": [{"role": "user", "content": PUMP}, {"role": "assistant", "content": "sure"}],
                    "user_message": "The old pump uses more electricity than the new pump.",
                }
            )
            payload = beliefs.scan()
        finally:
            root.removeHandler(handler)
        texts = [excerpt["text"] for excerpt in payload["excerpts"]]
        self.assertEqual(payload["excerpts"][0]["source"], "hook")
        self.assertIn(PUMP, texts)
        self.assertTrue(any("old pump uses more electricity" in text for text in texts))
        self.assertFalse(any("sure" == text for text in texts))
        self.assertTrue(all(PUMP not in message for message in records))


class FileTests(KeyedTestCase):
    def _service(self, public=False):
        service = FakeService(plugin)
        service.responses["list_collections"] = {
            "result": [
                {
                    "slug": "pump-notes",
                    "owner": service.pubkey,
                    "role": "owner",
                    "public": public,
                    "members": [service.pubkey],
                }
            ]
        }
        service.responses["search_claims"] = {
            "items": [
                {
                    "id": "claim-old",
                    "text": "something else",
                    "links": [
                        {"relation": "SUPPORTS", "quote": "18 Wh", "reason": "lower use", "from": "ev1"},
                        {"relation": "REBUTS", "quote": "24 Wh", "reason": "the old figure", "from": "ev2"},
                    ],
                }
            ]
        }
        return service

    def _propose(self):
        beliefs.record_consent(True)
        beliefs.scan(PUMP)
        return beliefs.propose(
            [
                {
                    "claim": CLAIM,
                    "quote": PUMP,
                    "assumptions": ["The two pumps were compared on the same load."],
                    "would_be_rebutted_by": "A same-load test where the new pump uses more electricity.",
                }
            ]
        )

    def test_a_quote_outside_the_excerpts_is_refused(self):
        beliefs.record_consent(True)
        beliefs.scan(PUMP)
        result = beliefs.propose(
            [
                {
                    "claim": CLAIM,
                    "quote": "This sentence was never said in the conversation.",
                    "assumptions": ["A hidden premise."],
                    "would_be_rebutted_by": "A measurement that goes the other way.",
                }
            ]
        )
        self.assertEqual(result["proposals"], [])
        self.assertIn("not in the excerpts", result["errors"][0]["error"])
        self.assertFalse(result["sent"])

    def test_approve_files_only_the_named_claim_in_a_private_collection(self):
        service = self._service()
        proposed = self._propose()
        settings = gates.read_settings(Ctx(mode="reader", collection="pump-notes"))
        result = beliefs.approve(service, settings, [proposed["proposals"][0]["id"]])
        self.assertEqual(result["collection"], "pump-notes")
        self.assertTrue(result["private"])
        filed = result["filed"][0]
        self.assertEqual(filed["claim"], CLAIM + ".")
        self.assertEqual(filed["assumptions"][0]["inference"], True)
        self.assertIn("agent inference", filed["assumptions"][0]["label"])
        self.assertEqual(len(filed["evidence"]["supports"]), 1)
        self.assertEqual(len(filed["evidence"]["rebuts"]), 1)
        self.assertEqual(len(service.published), 1)
        claim_event = next(event for event in service.published[0] if event["kind"] == 4702)
        content = json.loads(claim_event["content"])
        self.assertEqual(content, {"collection": "pump-notes", "text": CLAIM + "."})
        encoded = json.dumps(service.published)
        self.assertNotIn("would_be_rebutted_by", encoded)
        self.assertNotIn("assumption", encoded)
        self.assertNotIn("same load", encoded)
        self.assertEqual(len(service.published), 1)

    def test_a_second_approve_does_not_publish(self):
        service = self._service()
        proposed = self._propose()
        settings = gates.read_settings(Ctx(mode="reader"))
        beliefs.approve(service, settings, [proposed["proposals"][0]["id"]])
        with self.assertRaises(PluginError) as caught:
            beliefs.approve(service, settings, ["p1"])
        self.assertIn("already filed", str(caught.exception))
        self.assertEqual(len(service.published), 1)

    def test_a_public_collection_is_refused_before_publish(self):
        service = self._service(public=True)
        self._propose()
        settings = gates.read_settings(Ctx(mode="reader", collection="pump-notes"))
        with self.assertRaises(PluginError) as caught:
            beliefs.approve(service, settings, ["p1"], "pump-notes")
        self.assertIn("public", str(caught.exception))
        self.assertEqual(service.published, [])
        self.assertNotIn(("search_claims",), [call[:1] for call in service.calls])

    def test_tool_approve_needs_a_person_and_scan_does_not_send(self):
        self.assertIn("paranoy_surface_beliefs", schemas.BELIEF_TOOLS)
        self.assertIn("paranoy_surface_beliefs", schemas.COLLECTION_FREE)
        self.assertTrue(
            gates.tool_visible({"mode": "reader", "key_configured": True, "collection": ""}, "paranoy_surface_beliefs")
        )
        consent = gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_surface_beliefs", {"action": "consent"})
        self.assertEqual(consent["action"], "block")
        allowed = gates.pre_tool_directive(
            Ctx(mode="reader"), "paranoy_surface_beliefs", {"action": "consent", "consent": True}
        )
        self.assertEqual(allowed["action"], "approve")
        self.assertIn("Nothing is sent", allowed["message"])
        self.assertIsNone(
            gates.pre_tool_directive(Ctx(mode="reader"), "paranoy_surface_beliefs", {"action": "scan", "text": PUMP})
        )
        approve = gates.pre_tool_directive(
            Ctx(mode="reader"), "paranoy_surface_beliefs", {"action": "approve", "ids": ["p1"]}
        )
        self.assertEqual(approve["action"], "approve")
        self.assertIn("p1", approve["message"])
        cron = gates.pre_tool_directive(
            Ctx(cron=True, mode="reader"), "paranoy_surface_beliefs", {"action": "scan"}
        )
        self.assertEqual(cron["action"], "block")

    def test_slash_consent_scan_and_approve(self):
        ctx = Ctx(mode="reader", collection="pump-notes")
        self.assertIn("consent: no", gates.slash_result(ctx, "beliefs"))
        self.assertIn("Consent recorded", gates.slash_result(ctx, "beliefs consent"))
        scanned = gates.slash_result(ctx, "beliefs scan " + PUMP)
        self.assertIn("Excerpts read", scanned)
        self.assertIn(PUMP, scanned)
        beliefs.propose(
            [
                {
                    "claim": CLAIM,
                    "quote": PUMP,
                    "assumptions": ["Both pumps faced the same load."],
                    "would_be_rebutted_by": "A same-load test where the new pump uses more electricity.",
                }
            ]
        )
        service = self._service()
        text = gates.slash_result(ctx, "beliefs approve p1", lambda settings: service)
        self.assertIn("pump-notes", text)
        self.assertIn("supports 1", text)
        self.assertIn("agent inference", text)
        self.assertEqual(len(service.published), 1)

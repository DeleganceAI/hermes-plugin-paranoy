"""Capture pipeline tests: gates, publishing, dedupe, middleware, failure."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import FakeService, load_plugin

plugin = load_plugin()
capture = plugin.capture
state = plugin.state
runtime = plugin.runtime
PluginError = plugin.PluginError


class FakeLlm:
    """Stands in for ctx.llm: canned structured and chat completions."""

    def __init__(self, parsed=None, text="rewritten answer"):
        self.parsed = parsed or {}
        self.text = text
        self.calls = []

    def complete_structured(self, *, instructions, input, json_schema=None, json_mode=False, **kwargs):
        self.calls.append(instructions)
        return SimpleNamespace(parsed=dict(self.parsed), text=json.dumps(self.parsed))

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(text=self.text)


class FakeCtx:
    """No get_config on this Hermes; capture settings come from state."""

    def get_config(self, key, default=None):
        raise AttributeError("no config surface")

    llm = None

    def __init__(self, llm=None):
        self.llm = llm


def make_settings(**overrides):
    settings = {
        "server_url": "https://paranoy.org",
        "collection": "tesseralia",
        "mode": "contributor",
        "roles": "Scout",
        "key_configured": True,
        "auto_sign_terms": True,
        "terms_version": "1",
        "terms_sha256": "x" * 64,
        "require_publish_approval": True,
        "allow_unattended_writes": False,
        "agent_name": "",
        "agent_about": "",
    }
    settings.update(overrides)
    return settings


def enable_capture(**overrides):
    values = {
        "capture_enabled": True,
        "capture_extract": True,
        "capture_rewrite": True,
        "capture_platforms": "slack,terminal",
        "capture_collection": "tesseralia",
        "capture_permalink_base": "https://team.slack.com",
    }
    values.update(overrides)
    stored = state.load_state()
    stored["capture_settings"] = values
    state.save_state(stored)


class CaptureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        state.use_data_dir(Path(self._tmp.name))
        enable_capture()

    # -- helpers -----------------------------------------------------------

    def service(self, **responses):
        return FakeService(plugin, responses)

    def payload(self, text="I talked to customer X and they said they'd build it themselves.", **overrides):
        payload = {
            "platform": "slack",
            "chat_id": "C123",
            "message_id": "1731111111.000111",
            "user_name": "Dustin",
            "text": text,
            "collection": "tesseralia",
            "permalink": "https://team.slack.com/archives/C123/p1731111111000111",
            "max_claims": 3,
            "dupe_distance": 0.15,
        }
        payload.update(overrides)
        return payload

    # -- human path --------------------------------------------------------

    def test_observation_publishes_evidence_and_claims(self):
        llm = FakeLlm(
            parsed={
                "has_content": True,
                "observation": True,
                "claims": [
                    {
                        "text": "Customers who could build a comparable tool will not pay for it.",
                        "rebuttal": "Signed contracts from comparable builders.",
                    }
                ],
            }
        )
        service = self.service()
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        batches = service.published
        self.assertEqual(len(batches), 1)
        events = batches[0]
        kinds = [event["kind"] for event in events]
        self.assertIn(4701, kinds)
        self.assertIn(4702, kinds)
        evidence = next(event for event in events if event["kind"] == 4701)
        content = json.loads(evidence["content"])
        self.assertEqual(content["collection"], "tesseralia")
        self.assertEqual(content["source"], "Dustin")
        self.assertEqual(content["retrieved_via"], "slack")
        self.assertEqual(content["url"], "https://team.slack.com/archives/C123/p1731111111000111")

    def test_smalltalk_records_nothing(self):
        llm = FakeLlm(parsed={"has_content": False, "observation": False, "claims": []})
        service = self.service()
        capture.run_inbound(
            self.payload(text="ok sounds good!"), FakeCtx(llm), make_settings, lambda s: service
        )
        self.assertEqual(service.published, [])

    def test_duplicate_message_id_skipped(self):
        llm = FakeLlm(parsed={"has_content": True, "observation": True, "claims": []})
        service = self.service()
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        self.assertEqual(len(service.published), 1)
        journal = (state.data_dir() / "capture.jsonl").read_text()
        self.assertIn("inbound-duplicate", journal)

    def test_claim_without_rebuttal_is_dropped(self):
        llm = FakeLlm(
            parsed={
                "has_content": True,
                "observation": False,
                "claims": [{"text": "A claim.", "rebuttal": ""}],
            }
        )
        service = self.service()
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        self.assertEqual(service.published, [])

    def test_duplicate_claim_not_republished(self):
        llm = FakeLlm(
            parsed={
                "has_content": True,
                "observation": False,
                "claims": [
                    {
                        "text": "Customers who could build a comparable tool will not pay for it.",
                        "rebuttal": "Signed contracts.",
                    }
                ],
            }
        )
        service = self.service(
            search_claims={
                "items": [
                    {
                        "id": "c1",
                        "text": "Customers who could build a comparable tool will not pay for it.",
                    }
                ]
            }
        )
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        self.assertEqual(service.published, [])

    def test_semantic_near_duplicate_skipped(self):
        llm = FakeLlm(
            parsed={
                "has_content": True,
                "observation": False,
                "claims": [
                    {
                        "text": "Dollar spikes in 1985 and 2002 were followed by multi-year declines.",
                        "rebuttal": "Dollar index data showing sustained rises after those peaks.",
                    }
                ],
            }
        )
        service = self.service(
            search_claims={
                "items": [
                    {
                        "id": "c9",
                        "text": "The 1985 and 2002 dollar highs were followed by sharp falls.",
                        "distance": 0.09,
                    }
                ]
            }
        )
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        self.assertEqual(service.published, [])
        journal = (state.data_dir() / "capture.jsonl").read_text()
        self.assertIn("semantic", journal)
        self.assertIn("c9", journal)

    def test_claim_above_threshold_published(self):
        llm = FakeLlm(
            parsed={
                "has_content": True,
                "observation": False,
                "claims": [
                    {
                        "text": "Stablecoin dollarization could extend dollar dominance rather than erode it.",
                        "rebuttal": "Redemptions data showing stablecoin outflows into non-dollar assets.",
                    }
                ],
            }
        )
        service = self.service(
            search_claims={
                "items": [
                    {
                        "id": "c7",
                        "text": "Dollar-backed stablecoins make up about 99% of stablecoin market capitalization.",
                        "distance": 0.18,
                    }
                ]
            }
        )
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        self.assertEqual(len(service.published), 1)
        claims = [e for e in service.published[0] if e["kind"] == 4702]
        self.assertEqual(len(claims), 1)
        journal = (state.data_dir() / "capture.jsonl").read_text()
        self.assertIn("claim-kept", journal)

    def test_vector_search_failure_fails_open(self):
        llm = FakeLlm(
            parsed={
                "has_content": True,
                "observation": False,
                "claims": [
                    {
                        "text": "A brand new claim about the world.",
                        "rebuttal": "Evidence against it.",
                    }
                ],
            }
        )
        base = self.service()

        class FailingSearch(FakeService):
            def call(self, tool, args=None):
                if tool == "search_claims":
                    raise PluginError("vector search rate limited")
                return base.call(tool, args)

        service = FailingSearch(plugin)
        capture.run_inbound(self.payload(), FakeCtx(llm), make_settings, lambda s: service)
        claims = [e for batch in service.published for e in batch if e["kind"] == 4702]
        self.assertEqual(len(claims), 1)
        journal = (state.data_dir() / "capture.jsonl").read_text()
        self.assertIn("dedupe-unavailable", journal)

    def test_dupe_distance_setting_override(self):
        capture.set_capture_setting("capture_dupe_distance", 0.05)
        cfg = capture.read_capture_config(FakeCtx())
        self.assertEqual(cfg["dupe_distance"], 0.05)
        capture.set_capture_setting("capture_dupe_distance", 0.15)
        with self.assertRaises(PluginError):
            capture.set_capture_setting("capture_dupe_distance", 5)

    # -- middleware --------------------------------------------------------

    def response(self, text="The new pump used 18 Wh.", finish="stop", tools=None):
        message = SimpleNamespace(content=text, tool_calls=tools)
        choice = SimpleNamespace(finish_reason=finish, message=message)
        return SimpleNamespace(choices=[choice])

    def test_middleware_passes_through_when_tool_calls_present(self):
        service = self.service()

        def next_call(request):
            return self.response(tools=[{"id": "t1"}])

        result = capture.llm_execution(
            {"messages": []},
            next_call,
            ctx=FakeCtx(),
            settings_factory=make_settings,
            service_factory=lambda s: service,
            api_mode="",
            platform="slack",
        )
        self.assertEqual(result.choices[0].message.content, "The new pump used 18 Wh.")
        self.assertEqual(service.published, [])
        self.assertEqual(service.calls, [])

    def test_middleware_rewrites_final_answer(self):
        service = self.service(
            search_claims={
                "items": [
                    {
                        "id": "c1",
                        "text": "The new pump used less electricity in the two-hour test.",
                        "links": [
                            {
                                "relation": "SUPPORTS",
                                "quote": "the new pump used 18 Wh and the old pump used 24 Wh",
                                "reason": "The report records lower use.",
                            }
                        ],
                    }
                ]
            }
        )
        ctx = FakeCtx(FakeLlm(parsed={"has_content": False, "claims": []}, text="rewritten: pump used less"))

        def next_call(request):
            return self.response()

        result = capture.llm_execution(
            {"messages": [{"role": "user", "content": "does the new pump use less?"}]},
            next_call,
            ctx=ctx,
            settings_factory=make_settings,
            service_factory=lambda s: service,
            api_mode="",
            platform="slack",
        )
        self.assertEqual(result.choices[0].message.content, "rewritten: pump used less")
        search_args = dict(service.calls[0][1])
        self.assertEqual(search_args["collection"], "tesseralia")
        self.assertEqual(search_args["method"], "vector")

    def test_middleware_appends_warning_when_ledger_down(self):
        def broken_factory(settings):
            raise PluginError("Could not reach the Paranoy server.")

        def next_call(request):
            return self.response()

        result = capture.llm_execution(
            {"messages": []},
            next_call,
            ctx=FakeCtx(),
            settings_factory=make_settings,
            service_factory=broken_factory,
            api_mode="",
            platform="slack",
        )
        self.assertIn("ledger unreachable", result.choices[0].message.content)
        self.assertIn("The new pump used 18 Wh.", result.choices[0].message.content)

    def test_middleware_returns_draft_when_no_claims_found(self):
        service = self.service(search_claims={"items": []})
        ctx = FakeCtx(FakeLlm(parsed={"has_content": False, "claims": []}))

        def next_call(request):
            return self.response()

        result = capture.llm_execution(
            {"messages": []},
            next_call,
            ctx=ctx,
            settings_factory=make_settings,
            service_factory=lambda s: service,
            api_mode="",
            platform="slack",
        )
        self.assertEqual(result.choices[0].message.content, "The new pump used 18 Wh.")

    def test_middleware_disabled_passes_through(self):
        stored = state.load_state()
        stored["capture_settings"]["capture_enabled"] = False
        state.save_state(stored)
        service = self.service()
        calls = []

        def next_call(request):
            calls.append(request)
            return self.response()

        result = capture.llm_execution(
            {"messages": []},
            next_call,
            ctx=FakeCtx(),
            settings_factory=make_settings,
            service_factory=lambda s: service,
            api_mode="",
            platform="slack",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.choices[0].message.content, "The new pump used 18 Wh.")

    def test_middleware_extracts_claims_from_answer(self):
        stored = state.load_state()
        stored["capture_settings"]["capture_rewrite"] = False
        state.save_state(stored)
        service = self.service(search_claims={"items": []})
        ctx = FakeCtx(
            FakeLlm(
                parsed={
                    "has_content": True,
                    "claims": [
                        {
                            "text": "The new pump used 18 Wh in the two-hour test.",
                            "rebuttal": "The report's own measurements.",
                        }
                    ],
                }
            )
        )

        def next_call(request):
            return self.response()

        result = capture.llm_execution(
            {"messages": []},
            next_call,
            ctx=ctx,
            settings_factory=make_settings,
            service_factory=lambda s: service,
            api_mode="",
            platform="slack",
        )
        self.assertEqual(result.choices[0].message.content, "The new pump used 18 Wh.")
        self.assertEqual(len(service.published), 1)
        claims = [event for event in service.published[0] if event["kind"] == 4702]
        self.assertEqual(len(claims), 1)

    # -- config and eligibility -------------------------------------------

    def test_capture_requires_contributor_or_ledger_mode(self):
        settings = make_settings(mode="reader")
        eligible, _cfg, reason = capture.capture_eligible(FakeCtx(), settings)
        self.assertFalse(eligible)
        self.assertIn("contributor", reason)

    def test_capture_defaults_off(self):
        stored = state.load_state()
        del stored["capture_settings"]
        state.save_state(stored)
        eligible, _cfg, _reason = capture.capture_eligible(FakeCtx(), make_settings())
        self.assertFalse(eligible)

    def test_platform_gate(self):
        cfg = capture.read_capture_config(FakeCtx())
        self.assertTrue(capture.platform_allowed(cfg, "slack"))
        self.assertTrue(capture.platform_allowed(cfg, ""))
        self.assertTrue(capture.platform_allowed(cfg, "cli"))
        self.assertFalse(capture.platform_allowed(cfg, "telegram"))
        cfg_all = capture.read_capture_config(FakeCtx())
        self.assertTrue(capture.platform_allowed({**cfg_all, "platforms": {"all"}}, "irc"))

    def test_permalink_built_only_for_slack(self):
        cfg = capture.read_capture_config(FakeCtx())
        self.assertEqual(
            capture.build_permalink(cfg, "slack", "C123", "1731111111.000111"),
            "https://team.slack.com/archives/C123/p1731111111000111",
        )
        self.assertEqual(capture.build_permalink(cfg, "telegram", "C123", "1.2"), "")

    def test_inbound_payload_skips_commands_and_bots(self):
        event = SimpleNamespace(
            text="/paranoy status",
            internal=False,
            message_id="1",
            source=SimpleNamespace(
                platform=SimpleNamespace(value="slack"),
                chat_id="C1",
                user_name="D",
                is_bot=False,
            ),
        )
        event.is_command = lambda: True
        self.assertIsNone(capture.inbound_payload(event, None, FakeCtx(), make_settings))

    def test_slash_capture_toggles(self):
        capture.set_capture_setting("capture_enabled", False)
        cfg = capture.read_capture_config(FakeCtx())
        self.assertFalse(cfg["enabled"])
        capture.set_capture_setting("capture_enabled", True)
        cfg = capture.read_capture_config(FakeCtx())
        self.assertTrue(cfg["enabled"])

    def test_unknown_capture_setting_rejected(self):
        with self.assertRaises(PluginError):
            capture.set_capture_setting("capture_nonsense", "1")


if __name__ == "__main__":
    unittest.main()

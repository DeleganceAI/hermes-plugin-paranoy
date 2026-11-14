"""The behaviours SPEC.md promises: capture, ground, tools, decide, and outages."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeCtx, FakeLlm, FakeService, load_plugin, settings

plugin = load_plugin()
capture, ground, ledger, state, tools = plugin.capture, plugin.ground, plugin.ledger, plugin.state, plugin.tools
PluginError = plugin.client.PluginError

EXISTING = {"id": "c" * 64, "text": "The new pump used less power in the two-hour test."}
OPPOSITE = {"id": "d" * 64, "text": "The new pump used more power in the two-hour test."}


def contents(service, kind):
    return [json.loads(e["content"]) for batch in service.published for e in batch if e["kind"] == kind]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        state.use_data_dir(Path(self.tmp.name))
        state.update_state(terms_published=True)
        state.take_warnings()
        ground._DECIDING.clear()

    def tearDown(self):
        state.use_data_dir(None)
        self.tmp.cleanup()

    def journal(self):
        path = Path(self.tmp.name) / "journal.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class IngestTest(Base):
    def test_message_that_is_not_evidence_stores_nothing(self):
        service = FakeService(plugin)
        result = ledger.ingest(service, FakeLlm(), settings(), text="ok thanks", message=True)
        self.assertEqual(result, {"stored": False, "reason": "not evidence"})
        self.assertEqual(service.published, [])

    def test_evidence_with_no_claims_is_still_stored_without_links(self):
        service = FakeService(plugin)
        llm = FakeLlm({"evidence": True, "claims": []})
        result = ledger.ingest(service, llm, settings(), text="We went with Postgres.", message=True,
                               meta={"source": "Dustin", "retrieved_via": "utterance"})
        evidence = contents(service, 4701)
        self.assertEqual(evidence[0]["text"], "We went with Postgres.")
        self.assertEqual((evidence[0]["source"], evidence[0]["retrieved_via"]), ("Dustin", "utterance"))
        self.assertEqual(contents(service, 4702) + contents(service, 4703), [])
        self.assertTrue(result["stored"])

    def test_new_claims_are_stored_with_no_cap_and_no_links(self):
        service = FakeService(plugin, {"search_claims": {"items": []}})
        claims = [f"Claim number {n} holds." for n in range(7)]
        result = ledger.ingest(service, FakeLlm({"evidence": True, "claims": claims}), settings(), text="doc")
        self.assertEqual([c["text"] for c in contents(service, 4702)], claims)
        self.assertEqual(contents(service, 4703), [])
        self.assertEqual(len(result["claims"]), 7)

    def test_identical_text_reuses_the_claim_and_links_supports_without_a_quote(self):
        service = FakeService(plugin, {"search_claims": {"items": [EXISTING]}})
        llm = FakeLlm({"evidence": True, "claims": [EXISTING["text"]]})
        result = ledger.ingest(service, llm, settings(), text="doc")
        self.assertEqual(contents(service, 4702), [])
        (link,) = contents(service, 4703)
        self.assertEqual((link["to"], link["relation"], link["from"]), (EXISTING["id"], "SUPPORTS", result["evidence_id"]))
        self.assertNotIn("quote", link)
        self.assertEqual(len(llm.calls), 1, "identical text needs no model confirmation")

    def test_a_close_match_is_reused_only_when_the_model_confirms_it(self):
        service = FakeService(plugin, {
            "search_claims": lambda a: {"items": [] if a["method"] == "keyword" else [EXISTING, OPPOSITE]}})
        text = "In the two-hour test the new pump drew less power."
        confirmed = FakeLlm({"evidence": True, "claims": [text]}, matches=[{"claim": 0, "id": EXISTING["id"]}])
        result = ledger.ingest(service, confirmed, settings(), text="doc")
        self.assertEqual([link["to"] for link in contents(service, 4703)], [EXISTING["id"]])
        self.assertEqual(result["related_claims"], [OPPOSITE])

        service.published.clear()
        unconfirmed = FakeLlm({"evidence": True, "claims": [text]})
        result = ledger.ingest(service, unconfirmed, settings(), text="doc")
        self.assertEqual([c["text"] for c in contents(service, 4702)], [text])
        self.assertEqual(contents(service, 4703), [], "closeness alone never makes a link")
        self.assertEqual(result["related_claims"], [EXISTING, OPPOSITE])

    def test_a_model_match_outside_the_candidates_is_ignored(self):
        service = FakeService(plugin, {
            "search_claims": lambda a: {"items": [] if a["method"] == "keyword" else [OPPOSITE]}})
        llm = FakeLlm({"evidence": True, "claims": ["A claim."]}, matches=[{"claim": 0, "id": "f" * 64}])
        ledger.ingest(service, llm, settings(), text="doc")
        self.assertEqual(contents(service, 4703), [])

    def test_malformed_model_output_stores_no_junk_and_does_not_crash(self):
        service = FakeService(plugin, {
            "search_claims": lambda a: {"items": [] if a["method"] == "keyword" else [OPPOSITE]}})
        ledger.ingest(service, FakeLlm({"evidence": True, "claims": "The renewal moved."}), settings(), text="doc")
        self.assertEqual(contents(service, 4702), [])
        llm = FakeLlm({"evidence": True, "claims": ["A claim.", {"text": "x"}]}, matches=[{"claim": [0], "id": ["x"]}, "junk"])
        ledger.ingest(service, llm, settings(), text="doc")
        self.assertEqual([c["text"] for c in contents(service, 4702)], ["A claim."])
        self.assertEqual(contents(service, 4703), [])

    def test_first_write_sends_terms_and_profile_once(self):
        state.update_state(terms_published=False)
        service = FakeService(plugin)
        llm = FakeLlm({"evidence": True, "claims": []})
        ledger.ingest(service, llm, settings(), text="one")
        ledger.ingest(service, llm, settings(), text="two")
        self.assertEqual([[e["kind"] for e in batch] for batch in service.published], [[4706, 0, 4701], [4701]])


class CaptureTest(Base):
    def run_capture(self, payload, llm, service):
        capture.run(payload, FakeCtx(llm), settings(), lambda s: service)

    def test_gateway_message_is_stored_under_the_speakers_name(self):
        payload = capture.gateway_payload(
            {"text": "The renewal moved to March.", "platform": "slack", "message_id": "1.5",
             "source": {"user_name": "Ana", "chat_id": "C1"}},
            settings(permalink_base="https://team.slack.com"))
        service = FakeService(plugin)
        self.run_capture(payload, FakeLlm({"evidence": True, "claims": []}), service)
        (evidence,) = contents(service, 4701)
        self.assertEqual((evidence["source"], evidence["retrieved_via"]), ("Ana", "utterance"))
        self.assertEqual(evidence["url"], "https://team.slack.com/archives/C1/p15")
        self.assertTrue(evidence["title"].startswith("Ana, ") and evidence["title"].endswith(", slack"))
        self.run_capture(payload, FakeLlm({"evidence": True, "claims": []}), service)
        self.assertEqual(len(service.published), 1, "a re-sent message is not stored twice")

    def test_bots_commands_and_subagents_are_not_speech(self):
        cfg = settings()
        self.assertIsNone(capture.gateway_payload({"text": "hello there", "source": {"is_bot": True}}, cfg))
        self.assertIsNone(capture.gateway_payload({"text": "/paranoy status", "source": {}}, cfg))
        ctx = FakeCtx()
        self.assertIsNone(capture.terminal_payload({"user_message": "/decide x", "platform": "cli"}, ctx, cfg))
        self.assertIsNone(capture.terminal_payload({"user_message": "from slack", "platform": "slack"}, ctx, cfg))
        capture.note_subagent_started("child")
        self.assertIsNone(capture.terminal_payload({"user_message": "a task", "session_id": "child"}, ctx, cfg))
        capture.note_subagent_stopped("child")
        self.assertEqual(capture.terminal_payload({"user_message": "It broke.", "session_id": "s"}, ctx, cfg)["speaker"], "Dustin")

    def test_a_failed_capture_warns_on_the_next_reply(self):
        class Down(FakeService):
            def publish(self, events):
                raise PluginError("Could not reach the Paranoy server.")

        payload = {"platform": "terminal", "chat_id": "s", "message_id": "", "speaker": "Dustin", "text": "It broke.", "url": ""}
        self.run_capture(payload, FakeLlm({"evidence": True, "claims": []}), Down(plugin))
        capture.note_subagent_started("child")
        self.assertIsNone(ground.append_warnings("subagent output", "child"), "a subagent's output does not use it up")
        self.assertIsNone(ground.append_warnings(None, "s"), "nor does a reply with no text")
        capture.note_subagent_stopped("child")
        reply = ground.append_warnings("Sure.", "any-session")
        self.assertIn("Paranoy is unreachable", reply)
        self.assertIn("An earlier message was not recorded", reply)
        self.assertIsNone(ground.append_warnings("Sure.", "any-session"), "the warning is shown once per failure")

    def test_a_model_failure_is_not_called_an_outage(self):
        class Broken:
            def complete_structured(self, **kwargs):
                raise RuntimeError("no auxiliary model")

        payload = {"platform": "terminal", "chat_id": "s", "message_id": "", "speaker": "Dustin", "text": "It broke.", "url": ""}
        self.run_capture(payload, Broken(), FakeService(plugin))
        reply = ground.append_warnings("Sure.", "s")
        self.assertNotIn("unreachable", reply)
        self.assertIn("An earlier message was not recorded", reply)

    def test_the_same_words_in_a_later_turn_are_a_new_message(self):
        service = FakeService(plugin)
        llm = FakeLlm({"evidence": True, "claims": []})
        for turn in ("t1", "t2", "t2"):
            payload = capture.terminal_payload(
                {"user_message": "The build is broken again.", "session_id": "s", "turn_id": turn}, FakeCtx(), settings())
            self.run_capture(payload, llm, service)
        self.assertEqual(len(service.published), 2)
        self.assertEqual(self.journal()[-1]["reason"], "already handled")

    def test_a_nameless_speaker_is_never_recorded_as_the_owner(self):
        payload = capture.gateway_payload(
            {"text": "The server is down.", "platform": "slack", "source": {"user_id": "U42", "chat_id": "C1"}}, settings())
        self.assertEqual(payload["speaker"], "U42")

    def test_concurrent_captures_keep_state_whole(self):
        import threading

        plugin.settings.save(object(), "collection", "notes")
        errors = []

        def work(n):
            try:
                for i in range(40):
                    capture._first_sight({"platform": "slack", "chat_id": "c", "message_id": f"{n}-{i}"})
                    state.update_state(terms_published=True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        stored = state.load_state()
        self.assertEqual(errors, [])
        self.assertEqual(len(stored["capture_seen"]), 240)
        self.assertEqual(stored["plugin_settings"], {"collection": "notes"})


class GroundTest(Base):
    def turn(self, service, **changes):
        kwargs = {"user_message": "When is the renewal?", "session_id": "s1", "platform": "cli"}
        return ground.context(kwargs, FakeCtx(), settings(**changes), lambda s: service)

    def test_every_human_turn_gets_one_search_and_the_silent_rule(self):
        item = {**EXISTING, "links": [{"relation": "SUPPORTS", "from": "e" * 64, "reason": "says so"}],
                "contradiction_ids": ["9" * 64]}
        service = FakeService(plugin, {"search_claims": {"items": [item]}})
        block = self.turn(service)
        self.assertEqual(service.calls, [("search_claims", {"collection": "notes", "text": "When is the renewal?", "method": "vector"})])
        self.assertIn("Do not mention the ledger", block)
        self.assertIn(f"claim {EXISTING['id']}: {EXISTING['text']}", block)
        self.assertIn(f"SUPPORTS by evidence {'e' * 64}: says so", block)
        self.assertIn("in contradiction", block)
        self.assertIsNone(ground.append_warnings("The renewal is in March.", "s1"), "a grounded reply is left alone")
        self.assertEqual(self.journal()[-1]["claims"], [EXISTING["id"]])

    def test_an_empty_ledger_is_said_to_the_model(self):
        self.assertIn("found no claims", self.turn(FakeService(plugin, {"search_claims": {"items": []}})))

    def test_short_and_slash_led_messages_are_grounded_but_subagent_prompts_are_not(self):
        service = FakeService(plugin, {"search_claims": {"items": []}})
        for message in ("ok", "/etc/hosts is empty"):
            kwargs = {"user_message": message, "session_id": "s1"}
            self.assertIsNotNone(ground.context(kwargs, FakeCtx(), settings(), lambda s: service))
        capture.note_subagent_started("child")
        kwargs = {"user_message": "do the task", "session_id": "child"}
        self.assertIsNone(ground.context(kwargs, FakeCtx(), settings(), lambda s: service))
        capture.note_subagent_stopped("child")
        self.assertEqual(service.timeout, ground.SEARCH_TIMEOUT)
        self.assertLess(ground.SEARCH_TIMEOUT, 30, "must give up before Hermes skips the hook")

    def test_grounding_off_or_not_set_up_adds_nothing(self):
        service = FakeService(plugin)
        self.assertIsNone(self.turn(service, ground=False))
        self.assertIsNone(self.turn(service, ready=False))
        self.assertEqual(service.calls, [])

    def test_unreachable_ledger_still_replies_and_the_host_adds_the_warning(self):
        class Down(FakeService):
            def call(self, tool, args=None):
                raise PluginError("Could not reach the Paranoy server.")

        block = self.turn(Down(plugin))
        self.assertIn("could not be searched", block)
        reply = ground.append_warnings("Here is the regex.", "s1")
        self.assertTrue(reply.startswith("Here is the regex."))
        self.assertIn("Paranoy is unreachable", reply)
        self.assertIn("not grounded", reply)
        self.turn(Down(plugin))
        self.assertIsNone(ground.append_warnings("x", "other-session"), "another session's reply is not marked")
        self.assertIn("unreachable", ground.append_warnings("x", "s1"), "the warning repeats while the ledger is down")


class DecideTest(Base):
    def test_decide_starts_adds_the_procedure_and_ends_when_the_decision_is_recorded(self):
        service = FakeService(plugin, {"search_claims": {"items": []}})
        factory = lambda s: service
        self.assertIn("Decision mode is on", ground.decide("Postgres or SQLite?", "s1", settings(), factory))
        kwargs = {"user_message": "let's start", "session_id": "s1"}
        block = ground.context(kwargs, FakeCtx(), settings(ground=False), factory)
        self.assertIn("Postgres or SQLite?", block)
        self.assertIn("paranoy_check_proof", block)
        self.assertNotIn("Decision mode", ground.context({**kwargs, "session_id": "s2"}, FakeCtx(), settings(), factory))

        ctx = FakeCtx()
        specs = {s["name"]: s["handler"] for s in tools.build(ctx, settings, factory)}
        out = json.loads(specs["paranoy_store_evidence"](
            {"text": "Chose Postgres.", "source": "Dustin", "retrieved_via": "Decision"}, session_id="s1"))
        self.assertEqual(contents(service, 4701)[0]["retrieved_via"], "decision")
        self.assertIn("evidence_id", out)
        self.assertNotIn("Decision mode", ground.context(kwargs, FakeCtx(), settings(), factory))

    def test_decide_does_not_start_without_the_ledger(self):
        class Down(FakeService):
            def call(self, tool, args=None):
                raise PluginError("Could not reach the Paranoy server.")

        text = ground.decide("Postgres or SQLite?", "s1", settings(), lambda s: Down(plugin))
        self.assertIn("was not started", text)
        self.assertEqual(ground._DECIDING, {})


class ToolsTest(Base):
    def setUp(self):
        super().setUp()
        self.service = FakeService(plugin, {"search_claims": {"items": []}})
        self.ctx = FakeCtx(FakeLlm({"evidence": True, "claims": ["The page says X."]}))
        self.specs = {s["name"]: s for s in tools.build(self.ctx, settings, lambda s: self.service)}

    def call(self, name, **args):
        return json.loads(self.specs[name]["handler"](args))

    def test_the_tool_set_is_the_spec(self):
        self.assertEqual(sorted(self.specs), sorted([
            "paranoy_search", "paranoy_read", "paranoy_ingest", "paranoy_store_evidence",
            "paranoy_store_claim", "paranoy_check_proof"]))

    def test_search_passes_the_agents_limit_and_method(self):
        self.call("paranoy_search", query="pump power", method="keyword", limit=40)
        self.assertEqual(self.service.calls[-1], ("search_claims", {"collection": "notes", "text": "pump power", "method": "keyword", "limit": 40}))

    def test_ingest_url_fetches_stores_and_extracts(self):
        self.service.responses["fetch_url"] = {"text": "Page text.", "title": "A page"}
        out = self.call("paranoy_ingest", url="https://example.org/a")
        (evidence,) = contents(self.service, 4701)
        self.assertEqual((evidence["text"], evidence["title"], evidence["url"]), ("Page text.", "A page", "https://example.org/a"))
        self.assertEqual([c["text"] for c in out["claims"]], ["The page says X."])

    def test_ingest_evidence_id_reads_every_page_and_stores_no_second_copy(self):
        pages = {0: "a" * 20000, 20000: "tail"}
        self.service.responses["get_event"] = lambda a: {"result": {"kind": 4701, "chars": 20004, "content": {"text": pages[a["offset"]]}}}
        out = self.call("paranoy_ingest", evidence_id="e" * 64)
        self.assertEqual(contents(self.service, 4701), [])
        self.assertEqual(out["evidence_id"], "e" * 64)
        self.assertEqual([a["offset"] for t, a in self.service.calls if t == "get_event"], [0, 20000])

    def test_store_claim_alone_then_with_a_quote_free_link(self):
        out = self.call("paranoy_store_claim", text="A hypothesis.")
        self.assertEqual([e["kind"] for e in self.service.published[-1]], [4702])
        out = self.call("paranoy_store_claim", claim_id=out["claim_id"],
                        link={"evidence_id": "e" * 64, "relation": "rebuts", "reason": "It says otherwise."})
        (link,) = contents(self.service, 4703)
        self.assertEqual((link["relation"], link["to"]), ("REBUTS", out["claim_id"]))
        self.assertNotIn("quote", link)

    def test_bad_calls_return_errors_and_never_raise(self):
        self.assertIn("error", self.call("paranoy_store_claim"))
        self.assertIn("error", self.call("paranoy_store_claim", claim_id="c" * 64))
        self.assertIn("error", self.call("paranoy_ingest"))
        self.assertIn("secret key", self.call("paranoy_store_evidence", text="nsec1" + "q" * 20, source="x")["error"])
        self.assertEqual(self.service.published, [])
        self.assertEqual(self.journal()[-1]["kind"], "tool-error")


class HooksTest(Base):
    """The registered hooks, end to end, with a key stored and the server faked."""

    def setUp(self):
        super().setUp()
        import sys
        import types

        scope = types.ModuleType("agent.secret_scope")
        scope.get_secret = lambda name: "11" * 32
        self.modules = {"agent": types.ModuleType("agent"), "agent.secret_scope": scope}
        sys.modules.update(self.modules)
        self.service = FakeService(plugin, {"search_claims": {"items": [EXISTING]}})
        self.real_client = plugin.ParanoyClient
        plugin.ParanoyClient = lambda url, secret: self.service
        self.ctx = FakeCtx(FakeLlm({"evidence": True, "claims": []}), config={"collection": "notes"})
        plugin.register(self.ctx)

    def tearDown(self):
        import sys

        plugin.ParanoyClient = self.real_client
        for name in self.modules:
            sys.modules.pop(name, None)
        super().tearDown()

    def wait_for_capture(self):
        import threading

        for thread in threading.enumerate():
            if thread.name == "paranoy-capture":
                thread.join(5)

    def test_tools_appear_and_a_terminal_turn_is_grounded_and_captured(self):
        self.assertTrue(self.ctx.tools["paranoy_search"]["check_fn"]())
        out = self.ctx.hooks["pre_llm_call"](user_message="The renewal moved to March.", session_id="s", turn_id="t1")
        self.assertIn(EXISTING["text"], out["context"])
        self.wait_for_capture()
        self.assertEqual(contents(self.service, 4701)[0]["retrieved_via"], "utterance")
        self.assertIsNone(self.ctx.hooks["transform_llm_output"](response_text="Noted.", session_id="s"))

    def test_gateway_message_is_captured_and_an_outage_marks_the_reply(self):
        self.assertIsNone(self.ctx.hooks["post_gateway_admission"](
            text="Ana said the launch slipped.", platform="slack", message_id="1.2",
            source={"user_name": "Lee", "chat_id": "C1"}))
        self.wait_for_capture()
        self.assertEqual(contents(self.service, 4701)[0]["source"], "Lee")

        def down(tool, args=None):
            raise PluginError("Could not reach the Paranoy server.")

        self.service.call = down
        self.ctx.config["capture"] = False
        out = self.ctx.hooks["pre_llm_call"](user_message="When is launch?", session_id="s", platform="slack")
        self.assertIn("could not be searched", out["context"])
        reply = self.ctx.hooks["transform_llm_output"](response_text="I don't know.", session_id="s")
        self.assertIn("Paranoy is unreachable", reply)
        self.assertIn("unreachable", self.ctx.commands["decide"]("Ship now or wait?", session_id="s"))
        self.assertIn("server said", self.ctx.commands["paranoy"]("status"))


class RegisterTest(Base):
    def test_register_wires_hooks_tools_and_commands(self):
        ctx = FakeCtx(config={"collection": "notes"})
        plugin.register(ctx)
        self.assertEqual(sorted(ctx.tools), sorted(tools.NAMES))
        self.assertEqual(sorted(ctx.commands), ["decide", "paranoy"])
        for hook in ("pre_llm_call", "post_gateway_admission", "transform_llm_output"):
            self.assertIn(hook, ctx.hooks)
        self.assertFalse(ctx.tools["paranoy_search"]["check_fn"](), "tools stay hidden until a key is stored")
        self.assertIsNone(ctx.hooks["pre_llm_call"](user_message="hello there", session_id="s"))
        self.assertEqual(ctx.commands["paranoy"]("capture off"), "capture: off")
        self.assertIs(ctx.config["capture"], False)
        self.assertIn("key: none", ctx.commands["paranoy"]("status"))


if __name__ == "__main__":
    unittest.main()

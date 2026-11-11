"""Public catalog discovery and keygen registration. Network is mocked."""

import argparse
import json
import logging
import unittest

from tests.support import FakeService, load_plugin
from tests.test_profile import Ctx, KeyedTestCase

plugin = load_plugin()
public = plugin.public
gates = plugin.gates
schemas = plugin.schemas
PluginError = plugin.client.PluginError

CATALOG = {
    "collections": [
        {
            "slug": "mcas-hit",
            "topic": "Histamine Intolerance and MCAS",
            "created_at": "2026-10-21T14:48:54+00:00",
            "evidence": 5,
            "claims": 9,
            "links": 4,
            "contradictions": 1,
            "last_at": "2026-11-09T10:44:04+00:00",
            "contributors": 2,
            "shape": {"nodes": [1, 2, 3], "edges": [[0, 1, "SUPPORTS"]]},
            "daily": [0, 1, 2],
            "supports": 3,
        },
        {"slug": "grokbot", "topic": "Grok Bot", "evidence": 1, "claims": 1, "links": 0, "contradictions": 0},
        {"slug": "Not A Slug", "topic": "dropped"},
        "nope",
    ]
}


def mcp_ok(status, detail=""):
    body = {"collection": "ignored", "status": status}
    if detail:
        body["detail"] = detail
    return {"result": {"structuredContent": body}}


def mcp_error(text):
    return {"result": {"isError": True, "content": [{"type": "text", "text": text}]}}


class ChoiceTests(unittest.TestCase):
    def test_blank_means_all_and_slugs_are_deduped(self):
        self.assertEqual(public.parse_collection_choice(None), ("all", ()))
        self.assertEqual(public.parse_collection_choice(""), ("all", ()))
        self.assertEqual(public.parse_collection_choice(" ALL "), ("all", ()))
        self.assertEqual(public.parse_collection_choice("none"), ("none", ()))
        self.assertEqual(
            public.parse_collection_choice(" grokbot, mcas-hit, grokbot "),
            ("slugs", ("grokbot", "mcas-hit")),
        )

    def test_bad_choice_is_refused(self):
        for value in ("Bad Slug", "all, grokbot", "none,grokbot", "../etc"):
            with self.assertRaises(PluginError):
                public.parse_collection_choice(value)

    def test_flag_overrides_config(self):
        self.assertEqual(public.resolve_choice(None, "none"), ("none", ()))
        self.assertEqual(public.resolve_choice("grokbot", "none"), ("slugs", ("grokbot",)))
        self.assertEqual(public.resolve_choice(None, None), ("all", ()))


class CatalogTests(KeyedTestCase):
    def tearDown(self):
        public.use_transport()
        super().tearDown()

    def test_normalize_drops_display_fields_and_bad_rows(self):
        rows = public.normalize_public_rows(CATALOG)
        self.assertEqual([row["slug"] for row in rows], ["mcas-hit", "grokbot"])
        self.assertTrue(rows[0]["public"])
        self.assertNotIn("shape", rows[0])
        self.assertNotIn("daily", rows[0])
        self.assertEqual(rows[0]["evidence"], 5)
        self.assertEqual(rows[0]["topic"], "Histamine Intolerance and MCAS")

    def test_unreadable_catalog_is_an_error(self):
        with self.assertRaises(PluginError):
            public.normalize_public_rows(["mcas-hit"])
        with self.assertRaises(PluginError):
            public.showcase_url("paranoy.org")

    def test_fetch_caches_and_the_tool_marks_membership(self):
        seen = []

        def getter(url):
            seen.append(url)
            return CATALOG

        public.use_transport(getter=getter)
        service = FakeService(plugin)
        service.responses["list_collections"] = {
            "result": [{"slug": "grokbot", "members": [service.pubkey], "role": None}]
        }
        settings = gates.read_settings(Ctx(server_url="https://paranoy.example"))
        payload = public.list_for_agent(service, settings)
        self.assertEqual(seen, ["https://paranoy.example/showcase.json"])
        by_slug = {row["slug"]: row for row in payload["collections"]}
        self.assertFalse(by_slug["mcas-hit"]["member"])
        self.assertTrue(by_slug["grokbot"]["member"])
        self.assertNotIn("shape", json.dumps(payload))
        cached = public.cached_snapshot()
        self.assertEqual(cached["source"], "GET /showcase.json")
        self.assertEqual([row["slug"] for row in cached["collections"]], ["mcas-hit", "grokbot"])
        self.assertNotIn("member", cached["collections"][0])

    def test_a_failed_refresh_returns_the_cache(self):
        public.cache_public(public.normalize_public_rows(CATALOG), fetched_at="2026-11-11T00:00:00Z")

        def getter(url):
            raise PluginError("Could not reach the Paranoy server.")

        public.use_transport(getter=getter)
        service = FakeService(plugin)
        service.responses["list_collections"] = {"error": "down"}
        payload = public.list_for_agent(service, gates.read_settings(Ctx(server_url="https://paranoy.example")))
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["collections"][0]["slug"], "mcas-hit")
        self.assertIsNone(payload["collections"][0]["member"])

    def test_slash_public_lists_the_catalog(self):
        def getter(url):
            return CATALOG

        public.use_transport(getter=getter)
        ctx = Ctx(server_url="https://paranoy.example", mode="reader")
        service = FakeService(plugin)
        service.responses["list_collections"] = {"result": []}
        text = gates.slash_result(ctx, "public", lambda settings: service)
        self.assertIn("mcas-hit", text)
        self.assertIn("not a member", text)
        self.assertNotIn("nsec1", text)
        self.assertIn("Usage:", gates.slash_result(ctx, "public extra", lambda settings: service))


class RegisterTests(KeyedTestCase):
    def tearDown(self):
        public.use_transport()
        super().tearDown()

    def _client(self, secret, poster):
        return plugin.client.ParanoyClient(
            "https://paranoy.example",
            secret,
            poster=poster,
            clock=lambda: 1_700_000_000,
        )

    def test_register_key_signs_the_contract_and_hides_the_secret(self):
        secret = "ab" * 32
        seen = []

        def poster(url, payload):
            seen.append((url, payload))
            return mcp_ok("pending", "owner approval required")

        client = self._client(secret, poster)
        result = public.register_key("mcas-hit", client.pubkey.upper(), client=client)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["detail"], "owner approval required")
        self.assertEqual(seen[0][0], "https://paranoy.example/mcp")
        request = seen[0][1]["params"]["arguments"]["request"]
        content = json.loads(request["content"])
        self.assertEqual(content["tool"], "register_key")
        self.assertEqual(content["args"]["collection"], "mcas-hit")
        self.assertEqual(content["args"]["pubkey"], client.pubkey)
        self.assertEqual(list(seen[0][1]["params"]["arguments"]), ["request"])
        self.assertNotIn(secret, json.dumps(seen))
        self.assertNotIn("nsec1", json.dumps(seen))
        self.assertTrue(plugin.signing.verify_event(request))
        with self.assertRaises(PluginError):
            public.register_key("mcas-hit", "cd" * 32, client=client)
        self.assertEqual(len(seen), 1)

    def test_each_collection_is_reported_and_one_failure_does_not_stop_the_rest(self):
        secret = "cd" * 32
        calls = []

        def poster(url, payload):
            request = payload["params"]["arguments"]["request"]
            slug = json.loads(request["content"])["args"]["collection"]
            calls.append(slug)
            if slug == "mcas-hit":
                return mcp_error("This collection requires approval.")
            if slug == "grokbot":
                return mcp_ok("registered")
            return mcp_error("no such collection")

        client = self._client(secret, poster)
        rows = public.normalize_public_rows(CATALOG)
        # ufo is not public. The two catalog slugs are called even after a pending error.
        results = public.register_selection(client, ("slugs", ("mcas-hit", "ufo-uap-worldview", "grokbot")), rows)
        self.assertEqual(calls, ["mcas-hit", "grokbot"])
        by_slug = {row["collection"]: row["status"] for row in results}
        self.assertEqual(by_slug["mcas-hit"], "pending")
        self.assertEqual(by_slug["ufo-uap-worldview"], "skipped")
        self.assertEqual(by_slug["grokbot"], "registered")
        self.assertNotIn(secret, json.dumps(results))

    def test_offline_is_reported_for_every_remaining_collection(self):
        calls = []

        def poster(url, payload):
            calls.append(url)
            raise ConnectionError("down")

        client = self._client("ef" * 32, poster)
        rows = public.normalize_public_rows(CATALOG)
        results = public.register_selection(client, ("all", ()), rows)
        self.assertEqual(len(calls), 1)
        self.assertEqual([row["status"] for row in results], ["offline", "offline"])

    def test_errors_are_classified_without_crashing(self):
        secret = "11" * 32

        def classify(text):
            def poster(url, payload):
                return mcp_error(text)

            client = self._client(secret, poster)
            try:
                public.register_key("grokbot", client.pubkey, client=client)
            except PluginError as exc:
                return public.classify_error("grokbot", exc)["status"]

        self.assertEqual(classify("unknown tool: register_key"), "unavailable")
        self.assertEqual(classify("already a member of this collection"), "already")
        self.assertEqual(classify("already pending"), "already")
        self.assertEqual(classify("awaiting owner approval"), "pending")
        self.assertEqual(classify("the owner refused this key"), "rejected")

    def test_a_repeated_registration_is_already(self):
        secret = "22" * 32
        answers = [mcp_ok("registered"), mcp_error("duplicate request")]

        def poster(url, payload):
            return answers.pop(0)

        client = self._client(secret, poster)
        first = public.register_key("grokbot", client.pubkey, client=client)
        try:
            public.register_key("grokbot", client.pubkey, client=client)
            second = {"status": "registered"}
        except PluginError as exc:
            second = public.classify_error("grokbot", exc)
        self.assertEqual(first["status"], "registered")
        self.assertEqual(second["status"], "already")

    def test_keygen_stores_before_register_and_never_prints_the_secret(self):
        secret_box = {}
        order = []
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            def save(key, value):
                order.append("save")
                secret_box["key"] = key
                secret_box["value"] = value

            def getter(url):
                order.append("get")
                self.assertNotIn(secret_box["value"], url)
                return CATALOG

            def poster(url, payload):
                order.append("post")
                encoded = json.dumps(payload)
                self.assertNotIn(secret_box["value"], encoded)
                slug = json.loads(payload["params"]["arguments"]["request"]["content"])["args"]["collection"]
                if slug == "mcas-hit":
                    return mcp_error("approval required")
                return {"result": {"isError": True, "content": [{"text": "Could not reach the Paranoy server."}]}}

            text = public.keygen_from_settings(
                save,
                server_url="https://paranoy.example",
                cli_value=None,
                config_value="all",
                poster=poster,
                getter=getter,
            )
        finally:
            root.removeHandler(handler)
        secret = secret_box["value"]
        self.assertEqual(secret_box["key"], schemas.SECRET_ENV)
        self.assertEqual(order[0], "save")
        self.assertIn("get", order)
        self.assertNotIn(secret, text)
        self.assertNotIn("nsec1", text)
        self.assertIn("npub1", text)
        self.assertIn("mcas-hit: pending", text)
        self.assertIn("grokbot: offline", text)
        self.assertIn("The key was kept", text)
        self.assertIn("hermes paranoy register", text)
        state_text = (plugin.state.data_dir() / "state.json").read_text()
        self.assertNotIn(secret, state_text)
        self.assertIn("mcas-hit", state_text)
        self.assertNotIn("shape", state_text)
        self.assertTrue(all(secret not in message for message in records))

    def test_a_store_failure_does_not_touch_the_network(self):
        captured = {}

        def save(key, value):
            captured["value"] = value
            raise RuntimeError("store failed")

        def getter(url):
            raise AssertionError("catalog was fetched after a failed store")

        text = public.run_keygen(save, "https://paranoy.example", ("all", ()), getter=getter)
        self.assertIn("could not store", text)
        self.assertNotIn(captured["value"], text)
        self.assertNotIn("nsec1", text)

    def test_an_invalid_choice_does_not_generate_a_key(self):
        def save(key, value):
            raise AssertionError("saved")

        with self.assertRaises(PluginError):
            public.keygen_from_settings(
                save,
                server_url="https://paranoy.example",
                cli_value="Bad Slug",
                config_value="all",
            )

    def test_none_skips_registration_and_keeps_the_summary_clean(self):
        stored = {}

        def save(key, value):
            stored["value"] = value

        def getter(url):
            raise AssertionError("none does not list collections")

        text = public.keygen_from_settings(
            save,
            server_url="https://paranoy.example",
            cli_value="none",
            config_value="all",
            getter=getter,
        )
        self.assertIn("skipped (none)", text)
        self.assertNotIn(stored["value"], text)
        self.assertNotIn("nsec1", text)

    def test_discovery_failure_keeps_the_key(self):
        stored = {}

        def save(key, value):
            stored["value"] = value

        def getter(url):
            raise PluginError("Could not reach the Paranoy server.")

        text = public.run_keygen(save, "https://paranoy.example", ("all", ()), getter=getter)
        self.assertIn("npub1", text)
        self.assertIn("could not list public collections", text)
        self.assertNotIn(stored["value"], text)
        self.assertTrue(stored["value"])

    def test_register_stored_can_be_repeated(self):
        secret = "33" * 32
        calls = {"n": 0}

        def getter(url):
            return {"collections": [{"slug": "grokbot", "topic": "Grok"}]}

        def poster(url, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return mcp_ok("pending", "waiting")
            return mcp_error("already requested")

        first = public.register_stored(
            "https://paranoy.example", secret, ("all", ()), poster=poster, getter=getter
        )
        second_calls = calls["n"]

        def poster_again(url, payload):
            calls["n"] += 1
            return mcp_error("already a member")

        second = public.register_stored(
            "https://paranoy.example", secret, ("all", ()), poster=poster_again, getter=getter
        )
        self.assertEqual(first["results"][0]["status"], "pending")
        self.assertEqual(second["results"][0]["status"], "already")
        self.assertGreater(calls["n"], second_calls)
        self.assertNotIn(secret, json.dumps(first))
        self.assertNotIn(secret, json.dumps(second))

    def test_a_server_that_echoes_the_secret_is_redacted(self):
        secret = "44" * 32

        def save(key, value):
            self.assertEqual(value, secret)

        def getter(url):
            return {"collections": [{"slug": "grokbot", "topic": "Grok"}]}

        def poster(url, payload):
            return mcp_error("refused " + secret + " nsec1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq")

        # The client is built inside run_keygen, so the secret it redacts is the one it generated.
        # Drive register_stored with a known secret instead.
        report = public.register_stored(
            "https://paranoy.example", secret, ("slugs", ("grokbot",)), poster=poster, getter=getter
        )
        text = public.format_registration(report, secret_hex=secret)
        self.assertNotIn(secret, text)
        self.assertNotIn("nsec1qqq", text)
        self.assertIn("[redacted]", text)
        self.assertEqual(report["results"][0]["status"], "rejected")


class AgentSurfaceTests(KeyedTestCase):
    def tearDown(self):
        public.use_transport()
        super().tearDown()

    def test_the_tool_is_a_read_and_needs_no_approval(self):
        name = "paranoy_list_public_collections"
        self.assertIn(name, schemas.READ_TOOLS)
        self.assertIn(name, schemas.COLLECTION_FREE)
        self.assertIn(name, schemas.ALL_TOOL_NAMES)
        self.assertEqual(schemas.SLASH_TOOLS["public"], name)
        reader = {"mode": "reader", "key_configured": True, "collection": ""}
        self.assertTrue(gates.tool_visible(reader, name))
        self.assertFalse(gates.tool_visible({**reader, "key_configured": False}, name))
        self.assertIsNone(gates.resolve_collection(reader, name, {}))
        self.assertIsNone(gates.pre_tool_directive(Ctx(mode="reader"), name, {}))
        self.assertIn("member is true", schemas.SCHEMAS[name]["description"])

    def test_prompt_and_status_show_the_cache(self):
        public.cache_public(
            [{"slug": "grokbot", "topic": "Grok Bot", "public": True}],
            fetched_at="2026-11-11T00:00:00Z",
        )
        text = gates.prompt_context(gates.read_settings(Ctx(mode="reader", collection="pump")))
        self.assertIn("public collections: grokbot", text)
        self.assertIn("paranoy_list_public_collections", text)
        self.assertIn("register_public_collections: all", text)
        self.assertNotIn("nsec1", text)
        service = FakeService(plugin)
        service.responses["list_collections"] = {"result": []}
        service.responses["list_delegations"] = {"granted": [], "for": []}
        status = plugin.tools.read.status(service, gates.read_settings(Ctx()), {})
        self.assertEqual(status["public_collections"]["collections"][0]["slug"], "grokbot")
        self.assertEqual(status["register_public_collections"], "all")
        local = gates.status_text(gates.read_settings(Ctx(register_public_collections="none")))
        self.assertIn("register_public_collections: none", local)
        self.assertIn("public_collections: grokbot", local)
        self.assertNotIn("nsec1", local)

    def test_pinning_a_public_collection_says_membership_is_still_required(self):
        public.cache_public([{"slug": "grokbot", "public": True}])
        ctx = Ctx(mode="reader", collection="pump")
        service = FakeService(plugin)
        service.responses["list_collections"] = {
            "result": [{"slug": "pump", "members": [service.pubkey], "role": "member"}]
        }
        result = plugin.tools.config.set_collection(
            service, gates.read_settings(ctx), {"slug": "grokbot"}, ctx.set_config
        )
        self.assertIn("not a member of grokbot", result["error"])
        self.assertIn("public collection", result["error"])
        self.assertEqual(ctx.settings.get("collection"), "pump")

    def test_cli_exposes_the_collections_flag(self):
        ctx = Ctx()
        plugin.register(ctx)
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        paranoy = sub.add_parser("paranoy")
        ctx.cli_setup(paranoy)
        args = parser.parse_args(["paranoy", "keygen", "--collections", "mcas-hit,grokbot"])
        self.assertEqual(args.paranoy_command, "keygen")
        self.assertEqual(args.collections, "mcas-hit,grokbot")
        register = parser.parse_args(["paranoy", "register", "--collections", "none"])
        self.assertEqual(register.paranoy_command, "register")
        self.assertEqual(register.collections, "none")
        self.assertIn("terminal", gates.slash_result(Ctx(), "register"))
        self.assertIn("--collections", gates.slash_result(Ctx(), "keygen"))


if __name__ == "__main__":
    unittest.main()

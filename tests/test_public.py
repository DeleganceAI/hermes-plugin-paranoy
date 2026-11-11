"""Public catalog discovery and kind 4715 registration. Network is mocked."""

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
            "evidence": 5,
            "claims": 9,
            "links": 4,
            "contradictions": 1,
            "shape": {"nodes": [1, 2, 3]},
            "daily": [0, 1, 2],
            "contributors": 2,
        },
        {"slug": "grokbot", "topic": "Grok Bot", "evidence": 1, "claims": 1, "links": 0, "contradictions": 0},
        {"slug": "Not A Slug", "topic": "dropped"},
        "nope",
    ]
}


def api_getter(url):
    if not url.endswith("/api/public/collections"):
        raise AssertionError(url)
    return CATALOG


def register_response(event, collections=None, skipped=None):
    return {
        "ok": True,
        "pubkey": event["pubkey"],
        "npub": "npub1example",
        "role": "member",
        "collections": collections if collections is not None else [{"slug": "mcas-hit", "topic": "MCAS"}],
        "accepted": [event["id"]],
        "skipped": skipped or [],
    }


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
        self.assertEqual(public.registration_content(("all", ())), {"all": True})
        self.assertEqual(public.registration_content(("none", ())), {"collections": []})
        self.assertEqual(
            public.registration_content(("slugs", ("grokbot",))),
            {"collections": ["grokbot"]},
        )

    def test_bad_choice_is_refused(self):
        for value in ("Bad Slug", "all, grokbot", "none,grokbot", "../etc"):
            with self.assertRaises(PluginError):
                public.parse_collection_choice(value)
        too_many = ",".join(f"c{index}" for index in range(101))
        with self.assertRaises(PluginError) as caught:
            public.parse_collection_choice(too_many)
        self.assertIn("100", str(caught.exception))

    def test_flag_overrides_config(self):
        self.assertEqual(public.resolve_choice(None, "none"), ("none", ()))
        self.assertEqual(public.resolve_choice("grokbot", "none"), ("slugs", ("grokbot",)))
        self.assertEqual(public.resolve_choice(None, None), ("all", ()))


class CatalogTests(KeyedTestCase):
    def tearDown(self):
        public.use_transport()
        super().tearDown()

    def test_normalize_keeps_the_contract_fields(self):
        rows = public.normalize_public_rows(CATALOG)
        self.assertEqual([row["slug"] for row in rows], ["mcas-hit", "grokbot"])
        self.assertTrue(rows[0]["public"])
        self.assertNotIn("shape", rows[0])
        self.assertNotIn("daily", rows[0])
        self.assertNotIn("contributors", rows[0])
        self.assertEqual(rows[0]["evidence"], 5)
        self.assertEqual(rows[0]["topic"], "Histamine Intolerance and MCAS")

    def test_unreadable_catalog_is_an_error(self):
        with self.assertRaises(PluginError):
            public.normalize_public_rows(["mcas-hit"])
        with self.assertRaises(PluginError):
            public.origin("paranoy.org")

    def test_fetch_uses_the_public_api_and_caches_it(self):
        seen = []

        def getter(url):
            seen.append(url)
            return api_getter(url)

        public.use_transport(getter=getter)
        service = FakeService(plugin)
        service.responses["list_collections"] = {
            "result": [{"slug": "grokbot", "members": [service.pubkey], "role": None}]
        }
        settings = gates.read_settings(Ctx(server_url="https://paranoy.example"))
        payload = public.list_for_agent(service, settings)
        self.assertEqual(seen, ["https://paranoy.example/api/public/collections"])
        by_slug = {row["slug"]: row for row in payload["collections"]}
        self.assertFalse(by_slug["mcas-hit"]["member"])
        self.assertTrue(by_slug["grokbot"]["member"])
        self.assertNotIn("shape", json.dumps(payload))
        cached = public.cached_snapshot()
        self.assertEqual(cached["source"], "GET /api/public/collections")
        self.assertEqual([row["slug"] for row in cached["collections"]], ["mcas-hit", "grokbot"])

    def test_showcase_is_only_a_fallback_when_the_new_route_is_missing(self):
        def getter(url):
            if url.endswith("/api/public/collections"):
                raise public._MissingEndpoint("missing")
            if url.endswith("/showcase.json"):
                return CATALOG
            raise AssertionError(url)

        rows, source = public.fetch_public_collections("https://paranoy.example", getter=getter)
        self.assertEqual(source, "GET /showcase.json")
        self.assertEqual(rows[0]["slug"], "mcas-hit")

    def test_mcp_catalog_is_used_when_http_is_missing(self):
        def getter(url):
            raise public._MissingEndpoint(url)

        service = FakeService(plugin)
        service.responses["list_public_collections"] = {"collections": [{"slug": "grokbot", "topic": "Grok"}]}
        rows, source = public.fetch_public_collections(
            "https://paranoy.example", getter=getter, client=service
        )
        self.assertEqual(source, "MCP list_public_collections")
        self.assertEqual(rows[0]["slug"], "grokbot")
        self.assertEqual(service.calls[0][0], "list_public_collections")

    def test_a_failed_refresh_returns_the_cache(self):
        public.cache_public(
            public.normalize_public_rows(CATALOG),
            source="GET /api/public/collections",
            fetched_at="2026-11-11T00:00:00Z",
        )

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
        public.use_transport(getter=api_getter)
        ctx = Ctx(server_url="https://paranoy.example", mode="reader")
        service = FakeService(plugin)
        service.responses["list_collections"] = {"result": []}
        text = gates.slash_result(ctx, "public", lambda settings: service)
        self.assertIn("mcas-hit", text)
        self.assertIn("not a member", text)
        self.assertIn("/api/public/collections", text)
        self.assertNotIn("nsec1", text)
        self.assertIn("Usage:", gates.slash_result(ctx, "public extra", lambda settings: service))


class RegisterTests(KeyedTestCase):
    def tearDown(self):
        public.use_transport()
        super().tearDown()

    def test_all_signs_one_4715_and_hides_the_secret(self):
        secret = "ab" * 32
        seen = []

        def register_post(url, body):
            seen.append((url, body))
            return register_response(
                body["event"],
                collections=[{"slug": "mcas-hit", "topic": "MCAS"}, {"slug": "grokbot", "topic": "Grok"}],
                skipped=["blocked-one"],
            )

        report = public.register_stored(
            "https://paranoy.example",
            secret,
            ("all", ()),
            getter=api_getter,
            register_post=register_post,
        )
        self.assertIsNone(report["error"])
        self.assertEqual(seen[0][0], "https://paranoy.example/api/public/register")
        event = seen[0][1]["event"]
        self.assertEqual(event["kind"], 4715)
        self.assertEqual(json.loads(event["content"]), {"all": True})
        self.assertEqual(list(seen[0][1]), ["event"])
        self.assertNotIn(secret, json.dumps(seen))
        self.assertTrue(plugin.signing.verify_event(event))
        text = public.format_registration(report, secret_hex=secret)
        self.assertIn("role: member", text)
        self.assertIn("mcas-hit", text)
        self.assertIn("blocked-one", text)
        self.assertIn("immediate", text)
        self.assertIn("kind 4706", text)
        self.assertNotIn(secret, text)
        self.assertNotIn("waiting for approval", text)

    def test_none_clears_the_set_without_listing_the_catalog(self):
        def getter(url):
            raise AssertionError(url)

        seen = []

        def register_post(url, body):
            seen.append(json.loads(body["event"]["content"]))
            return register_response(body["event"], collections=[])

        report = public.register_stored(
            "https://paranoy.example",
            "cd" * 32,
            ("none", ()),
            getter=getter,
            register_post=register_post,
        )
        self.assertEqual(seen, [{"collections": []}])
        self.assertEqual(report["signed_content"], {"collections": []})
        self.assertIsNone(report["error"])

    def test_a_slug_list_drops_slugs_that_are_not_public(self):
        seen = []

        def register_post(url, body):
            seen.append(json.loads(body["event"]["content"]))
            return register_response(body["event"], collections=[{"slug": "grokbot", "topic": "Grok"}])

        report = public.register_stored(
            "https://paranoy.example",
            "ef" * 32,
            ("slugs", ("grokbot", "not-public")),
            getter=api_getter,
            register_post=register_post,
        )
        self.assertEqual(seen, [{"collections": ["grokbot"]}])
        self.assertEqual(report["omitted"], ["not-public"])
        text = public.format_registration(report)
        self.assertIn("not-public", text)

    def test_no_public_slug_does_not_send_an_empty_clear(self):
        def register_post(url, body):
            raise AssertionError("empty list would clear registration")

        report = public.register_stored(
            "https://paranoy.example",
            "11" * 32,
            ("slugs", ("not-public",)),
            getter=api_getter,
            register_post=register_post,
        )
        self.assertIn("no kind 4715", report["error"])

    def test_publish_is_the_fallback_when_register_is_missing(self):
        posted = []

        def register_post(url, body):
            raise public._MissingEndpoint("no register route")

        def mcp_poster(url, payload):
            posted.append((url, payload))
            event = payload["params"]["arguments"]["events"][0]
            self.assertEqual(event["kind"], 4715)
            return {"result": {"structuredContent": {"accepted": [event["id"]], "skipped": [], "collections": []}}}

        report = public.register_stored(
            "https://paranoy.example",
            "22" * 32,
            ("all", ()),
            getter=api_getter,
            register_post=register_post,
            mcp_poster=mcp_poster,
        )
        self.assertEqual(report["via"], "MCP publish")
        self.assertEqual(posted[0][0], "https://paranoy.example/mcp")
        self.assertEqual(json.loads(posted[0][1]["params"]["arguments"]["events"][0]["content"]), {"all": True})
        self.assertNotIn("22" * 32, json.dumps(posted))

    def test_a_repeat_sends_another_4715(self):
        contents = []

        def register_post(url, body):
            contents.append(json.loads(body["event"]["content"]))
            return register_response(body["event"])

        for _ in range(2):
            report = public.register_stored(
                "https://paranoy.example",
                "33" * 32,
                ("all", ()),
                getter=api_getter,
                register_post=register_post,
            )
            self.assertIsNone(report["error"])
        self.assertEqual(contents, [{"all": True}, {"all": True}])

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
                return api_getter(url)

            def register_post(url, body):
                order.append("post")
                encoded = json.dumps(body)
                self.assertNotIn(secret_box["value"], encoded)
                self.assertEqual(json.loads(body["event"]["content"]), {"all": True})
                return register_response(body["event"], skipped=["blocked-one"])

            text = public.keygen_from_settings(
                save,
                server_url="https://paranoy.example",
                cli_value=None,
                config_value="all",
                getter=getter,
                register_post=register_post,
            )
        finally:
            root.removeHandler(handler)
        secret = secret_box["value"]
        self.assertEqual(secret_box["key"], schemas.SECRET_ENV)
        self.assertEqual(order[0], "save")
        self.assertIn("post", order)
        self.assertNotIn(secret, text)
        self.assertNotIn("nsec1", text)
        self.assertIn("npub1", text)
        self.assertIn('{"all":true}', text)
        self.assertIn("blocked-one", text)
        state_text = (plugin.state.data_dir() / "state.json").read_text()
        self.assertNotIn(secret, state_text)
        self.assertIn("mcas-hit", state_text)
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

    def test_offline_registration_keeps_the_key(self):
        stored = {}

        def save(key, value):
            stored["value"] = value

        def register_post(url, body):
            raise PluginError("Could not reach the Paranoy server.")

        text = public.run_keygen(
            save,
            "https://paranoy.example",
            ("all", ()),
            getter=api_getter,
            register_post=register_post,
        )
        self.assertIn("npub1", text)
        self.assertIn("could not reach", text.lower())
        self.assertIn("hermes paranoy register", text)
        self.assertNotIn(stored["value"], text)

    def test_a_rate_limit_is_reported_without_losing_the_key(self):
        def save(key, value):
            return None

        def register_post(url, body):
            raise PluginError("too many registration posts this hour")

        text = public.run_keygen(
            save, "https://paranoy.example", ("none", ()), register_post=register_post
        )
        self.assertIn("30 registration posts", text)
        self.assertIn("10 stored events", text)
        self.assertIn("The key was kept", text)

    def test_a_server_that_echoes_the_secret_is_redacted(self):
        secret = "44" * 32

        def register_post(url, body):
            return {"ok": False, "error": "refused " + secret}

        report = public.register_stored(
            "https://paranoy.example",
            secret,
            ("none", ()),
            register_post=register_post,
        )
        # A 200 body is the response, not an exception. Put the secret in an error instead.
        report["error"] = "refused " + secret + " nsec1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
        report["response"] = None
        text = public.format_registration(report, secret_hex=secret)
        self.assertNotIn(secret, text)
        self.assertNotIn("nsec1qqq", text)
        self.assertIn("[redacted]", text)


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
            source="GET /api/public/collections",
            fetched_at="2026-11-11T00:00:00Z",
        )
        text = gates.prompt_context(gates.read_settings(Ctx(mode="reader", collection="pump")))
        self.assertIn("public collections: grokbot", text)
        self.assertIn("4715", text)
        self.assertIn("immediate", text)
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

    def test_pinning_a_public_collection_names_kind_4715(self):
        public.cache_public(
            [{"slug": "grokbot", "public": True}],
            source="GET /api/public/collections",
        )
        ctx = Ctx(mode="reader", collection="pump")
        service = FakeService(plugin)
        service.responses["list_collections"] = {
            "result": [{"slug": "pump", "members": [service.pubkey], "role": "member"}]
        }
        result = plugin.tools.config.set_collection(
            service, gates.read_settings(ctx), {"slug": "grokbot"}, ctx.set_config
        )
        self.assertIn("not a member of grokbot", result["error"])
        self.assertIn("4715", result["error"])
        self.assertNotIn("accepts this key", result["error"])
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

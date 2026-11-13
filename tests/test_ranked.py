"""Ranked answers use mocked ledger rows and do not invent claims."""

import unittest
from pathlib import Path

from tests.support import FakeService, load_plugin
from tests.test_profile import Ctx, KeyedTestCase

plugin = load_plugin()
ranked = plugin.ranked
gates = plugin.gates
schemas = plugin.schemas
PluginError = plugin.client.PluginError

QUESTION = ranked.DEMO_QUESTION


def evidence(evidence_id, title, url=None):
    row = {"id": evidence_id, "title": title, "source": title}
    if url:
        row["url"] = url
    return row


def link(relation, evidence_id):
    return {"relation": relation, "from": evidence_id, "quote": "quoted", "reason": "because"}


class RankedAnswerTests(KeyedTestCase):
    def service(self, items, collections=None, evidence_rows=None):
        service = FakeService(plugin)
        service.responses["list_collections"] = {
            "result": collections
            or [
                {
                    "slug": "seed-oils-and-sat-fat",
                    "topic": "Seed oils and saturated fat",
                    "public": True,
                    "role": "member",
                },
                {"slug": "ufo-uap-worldview", "topic": "UAP reports", "public": True, "role": "member"},
            ]
        }
        service.responses["search_claims"] = {"method": "vector", "items": items}
        service.responses["list_evidence"] = {
            "result": evidence_rows
            if evidence_rows is not None
            else [
                evidence("ev-soy-a", "Soy trial", "https://example.org/soy-a"),
                evidence("ev-soy-b", "Soy follow-up", "https://example.org/soy-b"),
                evidence("ev-soy-c", "Soy extra", "https://example.org/soy-c"),
                evidence("ev-soy-rebut", "Soy rebuttal", "https://example.org/soy-rebut"),
                evidence("ev-canola", "Canola note", "https://example.org/canola"),
                evidence("ev-olive", "Olive note"),
            ]
        }
        return service

    def items(self):
        return [
            {
                "id": "claim-canola",
                "text": "Canola oil raises LDL cholesterol more than olive oil.",
                "links": [link("SUPPORTS", "ev-canola")],
            },
            {
                "id": "claim-soy",
                "text": "Soybean oil raises LDL cholesterol more than olive oil.",
                "links": [
                    link("SUPPORTS", "ev-soy-a"),
                    link("SUPPORTS", "ev-soy-b"),
                    link("SUPPORTS", "ev-soy-c"),
                    link("REBUTS", "ev-soy-rebut"),
                    link("QUALIFIES", "ev-olive"),
                ],
            },
            {
                "id": "claim-tied",
                "text": "Sunflower oil changes LDL cholesterol.",
                "links": [link("SUPPORTS", "ev-olive"), link("REBUTS", "ev-canola")],
            },
        ]

    def test_demo_question_ranks_the_seed_oil_collection(self):
        service = self.service(self.items())
        settings = gates.read_settings(Ctx(server_url="https://paranoy.org"))
        result = ranked.answer(service, settings, {"question": QUESTION})
        self.assertEqual(result["collection"], "seed-oils-and-sat-fat")
        self.assertEqual(result["public"], True)
        self.assertEqual([option["id"] for option in result["options"]], ["claim-soy", "claim-canola", "claim-tied"])
        soy = result["options"][0]
        self.assertEqual(soy["supports"], 3)
        self.assertEqual(soy["rebuts"], 1)
        self.assertEqual(soy["qualifies"], 1)
        self.assertEqual(soy["net"], 2)
        self.assertEqual(len(soy["support_sources"]), 2)
        self.assertEqual(soy["support_sources"][0]["url"], "https://example.org/soy-a")
        self.assertEqual(soy["support_sources"][1]["url"], "https://example.org/soy-b")
        self.assertNotIn("https://example.org/soy-c", ranked.format_card(result))
        self.assertIn("Not on this claim", soy["would_be_rebutted_by"])
        self.assertIn("no url on the ledger", ranked.format_card(result))
        self.assertIn("supporting count minus rebutting count", result["card"])
        self.assertIn("Qualifying links are reported and do not change the net", result["card"])
        self.assertIn("not a verdict", result["card"])
        self.assertIn("https://paranoy.org/?c=seed-oils-and-sat-fat", result["card"])
        self.assertIn("Top claim id: claim-soy", result["card"])
        self.assertNotIn("the worst seed oil is", result["card"].lower())
        self.assertEqual(service.published, [])
        calls = [name for name, _ in service.calls]
        self.assertIn("list_collections", calls)
        self.assertIn("search_claims", calls)
        self.assertNotIn("publish", calls)

    def test_readme_contains_the_exact_demo_prompt(self):
        readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text()
        self.assertIn(QUESTION, readme)
        self.assertIn("/paranoy ask what is the worst seed oil and why", readme)

    def test_no_matching_claim_is_not_invented(self):
        service = self.service([])
        result = ranked.answer(service, gates.read_settings(Ctx()), {"question": QUESTION})
        self.assertEqual(result["options"], [])
        self.assertIn("no claim that answers", result["card"])
        self.assertIn("Nothing was added", result["card"])
        self.assertNotIn("Soybean", result["card"])

    def test_no_matching_collection_lists_what_the_key_can_see(self):
        service = self.service([], collections=[{"slug": "ufo-uap-worldview", "topic": "UAP reports", "public": True}])
        with self.assertRaises(PluginError) as caught:
            ranked.answer(service, gates.read_settings(Ctx()), {"question": QUESTION})
        self.assertIn("No listed collection matches", str(caught.exception))
        self.assertIn("ufo-uap-worldview", str(caught.exception))
        self.assertEqual(service.published, [])

    def test_keyword_is_used_when_vector_search_fails(self):
        service = self.service(self.items())

        def search(args):
            if args.get("method") == "vector":
                raise PluginError("vector search is rate limited")
            return {"method": "keyword", "items": self.items()[:1]}

        service.responses["search_claims"] = search
        result = ranked.answer(service, gates.read_settings(Ctx()), {"question": QUESTION})
        self.assertEqual(result["search"], "keyword")
        self.assertEqual(len(result["options"]), 1)
        methods = [args.get("method") for name, args in service.calls if name == "search_claims"]
        self.assertEqual(methods, ["vector", "keyword"])

    def test_missing_links_are_loaded_and_a_stored_rebuttal_is_quoted(self):
        service = self.service([])
        service.responses["search_claims"] = {
            "items": [
                {
                    "id": "claim-soy",
                    "text": "Soybean oil raises LDL cholesterol more than olive oil.",
                    "would_be_rebutted_by": "A trial where soybean oil lowers LDL.",
                }
            ]
        }
        service.responses["get_links"] = {
            "result": [link("SUPPORTS", "ev-missing"), link("REBUTS", "ev-soy-rebut")]
        }
        service.responses["get_event"] = {
            "result": {"content": {"title": "Missing paper", "url": "https://example.org/missing"}}
        }
        result = ranked.answer(
            service, gates.read_settings(Ctx()), {"question": QUESTION, "collection": "seed-oils-and-sat-fat"}
        )
        option = result["options"][0]
        self.assertEqual(option["net"], 0)
        self.assertEqual(option["would_be_rebutted_by"], "A trial where soybean oil lowers LDL.")
        self.assertEqual(option["support_sources"][0]["url"], "https://example.org/missing")
        self.assertIn("thin", option["thinness"])

    def test_one_sided_and_missing_evidence_are_named(self):
        self.assertIn("missing", ranked._thinness(0, 0, 0))
        self.assertIn("one-sided toward support", ranked._thinness(4, 0, 0))
        self.assertIn("one-sided toward rebuttal", ranked._thinness(0, 2, 0))
        self.assertIn("both present", ranked._thinness(3, 2, 1))

    def test_slash_ask_prints_the_card(self):
        service = self.service(self.items())
        ctx = Ctx(server_url="https://paranoy.org")
        text = gates.slash_result(ctx, "ask " + QUESTION, lambda settings: service)
        self.assertIn("Evidence rank in seed-oils-and-sat-fat", text)
        self.assertIn("net 2", text)
        self.assertTrue(gates.tool_visible({"mode": "reader", "key_configured": True}, "paranoy_ranked_answer"))
        self.assertIsNone(gates.pre_tool_directive(ctx, "paranoy_ranked_answer", {"question": QUESTION}))
        self.assertIn("paranoy_ranked_answer", schemas.RANKED_TOOLS)
        self.assertIn("paranoy_ranked_answer", schemas.COLLECTION_FREE)
        self.assertEqual(schemas.SLASH_TOOLS["ask"], "paranoy_ranked_answer")

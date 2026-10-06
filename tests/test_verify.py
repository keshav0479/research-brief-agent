import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from brief_agent.clients import ProviderError
from brief_agent.verify import verify_claim


def answer(choice, confidence=0.95):
    return {"type": "choice", "choice": choice, "confidence": confidence}


def pair(relation="supports", kind="reported_fact", confidence=0.95):
    return {"relation": answer(relation, confidence), "kind": answer(kind)}


def document(sid="S1", tier=2):
    return SimpleNamespace(sid=sid, tier=tier, body="Full document text.",
                           source="Example issuer", source_type="official company filing",
                           published="2026-08-14",
                           windows={f"{sid}.u01": "Selected sentence with context."})


class FakeDecider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((copy.deepcopy(state), copy.deepcopy(questions)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "prompts" / "jev_questions.json"
        self.questions = json.loads(path.read_text())
        self.claim = {"text": "Revenue increased.", "kind": "reported_fact", "cites": ["S1.u01"]}

    def run_check(self, responses, docs=None, section="bull", evidence_ids=None):
        docs = docs or [document()]
        decider = FakeDecider(responses)
        result = verify_claim(self.claim, docs,
                              evidence_ids if evidence_ids is not None else {d.sid for d in docs},
                              section, self.questions, decider)
        return result, decider

    def test_support_and_kind_share_one_call_and_use_windows(self):
        result, decider = self.run_check([pair()])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(decider.calls), 1)
        state, questions = decider.calls[0]
        self.assertEqual(set(questions), {"relation", "kind"})
        self.assertEqual(state["statement"], self.claim["text"])
        self.assertIn("[S1.u01]", state["source_document"])
        self.assertIn("Selected sentence with context.", state["source_document"])
        self.assertIn("Source: Example issuer", state["source_document"])
        self.assertIn("Source type: official company filing", state["source_document"])
        self.assertIn("Published: 2026-08-14", state["source_document"])
        self.assertIn("not necessarily the event date", state["source_document"])
        self.assertEqual(questions["kind"], self.questions["kinds"]["kind"])

    def test_uncertain_support_reverses_only_its_options_and_accepts_stable_high(self):
        result, decider = self.run_check([pair(confidence=0.5), {"relation": answer("supports", 0.8)}])
        self.assertFalse(result["errors"])
        self.assertEqual(list(decider.calls[1][1]), ["relation"])
        self.assertEqual(list(decider.calls[1][1]["relation"]["criteria"]),
                         list(reversed(self.questions["verify"]["relation"]["criteria"])))
        self.assertEqual(result["diagnostics"][0]["answers"]["relation"]["confidence"], 0.5)
        self.assertEqual(result["diagnostics"][0]["retry_answers"]["relation"]["confidence"], 0.8)

    def test_changed_or_still_uncertain_choice_is_unresolved(self):
        for retry in (answer("contradicts"), answer("supports", 0.7)):
            with self.subTest(retry=retry):
                result, decider = self.run_check([pair(confidence=0.5), {"relation": retry}])
                self.assertTrue(any("unresolved" in error for error in result["errors"]))
                self.assertEqual(len(decider.calls), 2)

    def test_confident_non_support_is_rejected_without_retry(self):
        for relation in ("contradicts", "says_nothing"):
            result, decider = self.run_check([pair(relation)])
            self.assertIn(relation, result["errors"][0])
            self.assertEqual(len(decider.calls), 1)

    def test_kind_is_relabelled_but_forecast_cannot_be_snapshot(self):
        result, _ = self.run_check([pair(kind="management_view_or_forecast")], section="snapshot")
        self.assertEqual(result["kind"], "management_view_or_forecast")
        self.assertTrue(result["kind_confirmed"])
        self.assertTrue(any("Snapshot" in error for error in result["errors"]))

    def test_uncertain_kind_retains_writer_label_outside_snapshot_only(self):
        first = pair(confidence=1)
        first["kind"] = answer("management_view_or_forecast", 0.5)
        retry = {"kind": answer("management_view_or_forecast", 0.6)}
        for section in ("bull", "bear", "open_questions", "snapshot"):
            with self.subTest(section=section):
                result, _ = self.run_check([first, retry], section=section)
                self.assertEqual(result["kind"], self.claim["kind"])
                self.assertFalse(result["kind_confirmed"])
                self.assertEqual(result["diagnostics"][-1]["status"], "unconfirmed")
                self.assertEqual(bool(result["errors"]), section == "snapshot")

    def test_uncertain_kind_never_clears_an_unsupported_relation(self):
        first = pair("says_nothing")
        first["kind"] = answer("reported_fact", 0.4)
        result, _ = self.run_check([first, {"kind": answer("reported_fact", 0.6)}])
        self.assertFalse(result["kind_confirmed"])
        self.assertIn("Cited evidence relation: says_nothing.", result["errors"])

    def test_uncertain_kind_never_clears_a_higher_tier_contradiction(self):
        first = pair(confidence=1)
        first["kind"] = answer("reported_fact", 0.4)
        result, _ = self.run_check(
            [first, {"kind": answer("reported_fact", 0.6)}, {"relation": answer("contradicts")}],
            [document(tier=3), document("S2", 1)])
        self.assertFalse(result["kind_confirmed"])
        self.assertTrue(result["conflicts"])
        self.assertTrue(result["errors"])

    def test_confident_kind_mismatch_relabels_outside_snapshot(self):
        result, _ = self.run_check([pair(kind="management_view_or_forecast")])
        self.assertEqual(result["kind"], "management_view_or_forecast")
        self.assertTrue(result["kind_confirmed"])
        self.assertFalse(result["errors"])

    def test_uncertain_kind_rechecks_and_preserves_questions(self):
        first = pair()
        first["kind"] = answer("management_view_or_forecast", 0.3)
        original = copy.deepcopy(self.questions)
        result, decider = self.run_check([first, {"kind": answer("management_view_or_forecast")}])
        self.assertFalse(result["errors"])
        self.assertEqual(result["kind"], "management_view_or_forecast")
        self.assertEqual(list(decider.calls[1][1]), ["kind"])
        self.assertEqual(self.questions, original)

    def test_low_tier_claim_checks_each_admitted_high_tier_document(self):
        docs = [document(tier=3), document("S2", 1), document("S3", 2), document("S4", 2)]
        result, decider = self.run_check(
            [pair(), {"relation": answer("says_nothing")}, {"relation": answer("contradicts")}],
            docs, evidence_ids={"S1", "S2", "S3"})
        self.assertEqual(len(decider.calls), 3)
        high_tier_context = decider.calls[1][0]["source_document"]
        self.assertIn("[S2]", high_tier_context)
        self.assertIn("Full document text.", high_tier_context)
        self.assertIn("Source: Example issuer", high_tier_context)
        self.assertIn("Published: 2026-08-14", high_tier_context)
        self.assertEqual(result["conflicts"][0]["higher_tier_source"], "S3")
        self.assertEqual(result["conflicts"][0]["cited_sources"], ["S1"])
        self.assertEqual(len(result["errors"]), 1)

    def test_low_confidence_cross_tier_silence_cannot_clear_claim(self):
        result, _ = self.run_check(
            [pair(), {"relation": answer("says_nothing", 0.5)},
             {"relation": answer("says_nothing", 0.6)}], [document(tier=4), document("S2", 1)])
        self.assertTrue(any("higher-tier source S2 is unresolved" in error for error in result["errors"]))

    def test_provider_failure_on_retry_is_not_clean(self):
        result, _ = self.run_check([pair(confidence=0.5), ProviderError("timeout")])
        self.assertTrue(result["unavailable"])
        self.assertTrue(result["errors"])
        self.assertEqual(result["diagnostics"][-1]["error"], "timeout")

    def test_unknown_or_excluded_citation_skips_provider(self):
        result, decider = self.run_check([], evidence_ids=set())
        self.assertTrue(result["errors"])
        self.assertFalse(decider.calls)

    def test_open_question_is_checked_as_premise_not_answer(self):
        result, decider = self.run_check([pair()], section="open_questions")
        self.assertFalse(result["errors"])
        relation = decider.calls[0][1]["relation"]
        self.assertEqual(relation, self.questions["verify_open_questions"]["relation"])
        self.assertIn("factual premises", relation["instructions"])
        self.assertEqual(decider.calls[0][1]["kind"], self.questions["open_question_kinds"]["kind"])
        self.assertNotIn("evaluation_note", decider.calls[0][0])

    def test_open_question_can_retain_management_premise_without_becoming_fact(self):
        self.claim["text"] = "Management expects lower costs. What could prevent that?"
        result, _ = self.run_check([pair(kind="management_view_or_forecast")], section="open_questions")
        self.assertEqual(result["kind"], "management_view_or_forecast")
        self.assertFalse(result["errors"])

    def test_open_question_kind_retry_uses_same_premise_definition_and_confirmation_gate(self):
        first = pair()
        first["kind"] = answer("reported_fact", 0.79)
        result, decider = self.run_check([first, {"kind": answer("reported_fact", 0.79)}], section="open_questions")
        self.assertFalse(result["errors"])
        self.assertFalse(result["kind_confirmed"])
        self.assertEqual(result["diagnostics"][-1]["status"], "unconfirmed")
        original = self.questions["open_question_kinds"]["kind"]
        retried = decider.calls[1][1]["kind"]
        self.assertEqual(retried["instructions"], original["instructions"])
        self.assertEqual(list(retried["criteria"]), list(reversed(original["criteria"])))

    def test_open_question_with_unsupported_premise_still_fails(self):
        self.claim["text"] = "Management has acquired a competitor. Will integration be expensive?"
        result, _ = self.run_check([pair("says_nothing")], section="open_questions")
        self.assertTrue(result["errors"])

    def test_open_question_threshold_and_cross_tier_semantics_remain_strict(self):
        result, decider = self.run_check(
            [pair(confidence=0.79), {"relation": answer("supports", 0.79)},
             {"relation": answer("contradicts")}],
            [document(tier=3), document("S2", 1)], section="open_questions")
        self.assertTrue(any("unresolved" in error for error in result["errors"]))
        self.assertEqual(result["conflicts"][0]["higher_tier_source"], "S2")
        self.assertEqual(decider.calls[2][1]["relation"], self.questions["verify_open_questions"]["relation"])


if __name__ == "__main__":
    unittest.main()

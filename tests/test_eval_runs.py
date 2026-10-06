import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from eval_runs import aggregate, human_metrics, interval, review_template, usage_totals


class EvaluationTests(unittest.TestCase):
    def test_token_aliases_are_not_double_counted(self):
        timing = {"writer_attempts": 1, "jev_attempts": 1, "usage": [
            {"usage": {"prompt_tokens": 10, "input_tokens": 10, "completion_tokens": 5,
                       "output_tokens": 5, "total_tokens": 15}},
            {"usage": {"input_tokens": 7, "output_tokens": 3}}]}
        self.assertEqual(usage_totals(timing), {"input": 17, "output": 8, "total": 25, "partial": False})

    def test_unreported_tokens_remain_unknown_and_partial_calls_are_marked(self):
        self.assertIsNone(usage_totals({})["total"])
        result = usage_totals({"writer_attempts": 2, "usage": [{"usage": {"total_tokens": 14}}]})
        self.assertEqual(result["total"], 14)
        self.assertIsNone(result["input"])
        self.assertTrue(result["partial"])

    def test_reported_zero_tokens_are_preserved(self):
        result = usage_totals({"writer_attempts": 1, "usage": [{"usage": {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}]})
        self.assertEqual(result, {"input": 0, "output": 0, "total": 0, "partial": False})

    def test_intervals_recompute_from_counts_and_zero_is_not_perfect(self):
        self.assertEqual(interval({"k": 5, "n": 10, "low": 0, "high": 1}), "5/10 [23.7%, 76.3%]")
        self.assertEqual(interval({"k": 0, "n": 0}), "not measured")
        self.assertEqual(interval({"k": True, "n": 1}), "not measured")

    def test_human_support_requires_explicit_reviewer_and_boolean_labels(self):
        labels = {"supported_sample": [{"id": "a", "text": "A claim.", "pass": True}]}
        self.assertIn("reviewer missing", human_metrics(labels)[0])
        labels["reviewer"] = "Reviewer"
        labels["reviewer_type"] = "human"
        labels["supported_sample"].extend([
            {"id": "b", "text": "B claim.", "pass": "true"},
            {"id": "c", "text": "C claim.", "pass": None},
            {"id": "d", "text": "D claim.", "pass": False}])
        support, traps, rounding = human_metrics(labels)
        self.assertTrue(support.startswith("1/2 ["))
        self.assertIn("2 pending", support)
        self.assertEqual((traps, rounding), ("pending", "pending"))

    def test_model_annotations_do_not_count_as_human_labels(self):
        labels = {"reviewer": "Codex", "reviewer_type": "model", "supported_sample": [
            {"id": "a", "text": "A claim.", "pass": True}],
            "traps": {"company_identity": {"pass": True}}, "rounding_false_alarms": {"count": 0}}
        support, traps, rounding = human_metrics(labels)
        self.assertIn("human review required", support)
        self.assertEqual((traps, rounding), ("pending", "pending"))

    def test_templates_preserve_null_labels_and_keep_rounding_separate(self):
        template = review_template(Path("D1"), {}, {"accepted": {"snapshot": [{"text": "Revenue rose."}]}})
        self.assertEqual(template["supported_sample"][0], {"id": "snapshot.1", "text": "Revenue rose.", "pass": None, "note": ""})
        self.assertIn("scope", template["traps"])
        self.assertTrue(all(item["pass"] is None for item in template["traps"].values()))
        self.assertIsNone(template["rounding_false_alarms"]["count"])

    def test_duplicate_samples_are_not_counted(self):
        labels = {"reviewer": "Reviewer", "reviewer_type": "human", "supported_sample": [
            {"id": "a", "text": "A.", "pass": True}, {"id": "a", "text": "A.", "pass": True}]}
        self.assertIn("invalid", human_metrics(labels)[0])

    def test_baseline_is_unmeasured_and_default_does_not_write_run_files(self):
        with TemporaryDirectory() as tmp:
            runs = Path(tmp)
            folder = runs / "A1"
            folder.mkdir()
            (folder / "config.json").write_text(json.dumps({"arm": "A"}))
            (folder / "checks.json").write_text(json.dumps({"status": "completed_unchecked", "word_count": 123,
                "metrics": {"numbers_matched": {"k": 7, "n": 7}}}))
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            report = aggregate(runs)
            row = next(line for line in report.splitlines() if line.startswith("| A1 | A |"))
            self.assertIn("not measured", row)
            self.assertNotIn("7/7", row)
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            aggregate(runs, write_templates=True)
            template = folder / "review-labels.template.json"
            template.write_text("Keep my edits.")
            aggregate(runs, write_templates=True)
            self.assertEqual(template.read_text(), "Keep my edits.")

    def test_corrupt_run_is_visible_without_hiding_healthy_run(self):
        with TemporaryDirectory() as tmp:
            runs = Path(tmp)
            broken, healthy = runs / "broken", runs / "healthy"
            broken.mkdir()
            healthy.mkdir()
            (broken / "config.json").write_text("{")
            (healthy / "config.json").write_text('{"arm": "D"}')
            (healthy / "checks.json").write_text('{"status": "completed", "metrics": {"cited_ids_valid": {"k": 2, "n": 3}}}')
            report = aggregate(runs)
            self.assertIn("unreadable artifacts", report)
            self.assertIn("2/3 [", report)

    def test_labels_for_another_run_are_not_used(self):
        with TemporaryDirectory() as tmp:
            runs = Path(tmp)
            folder = runs / "D1"
            folder.mkdir()
            (folder / "review-labels.json").write_text(json.dumps({"run": "D2", "reviewer": "R",
                "supported_sample": [{"id": "a", "text": "A.", "pass": True}]}))
            report = aggregate(runs)
            self.assertIn("review labels name another run", report)
            self.assertNotIn("1/1 [", report)

    def test_different_code_versions_are_counted_separately(self):
        with TemporaryDirectory() as tmp:
            runs = Path(tmp)
            for label, manifest in [("D1", "one"), ("D2", "one"), ("D3", "two")]:
                folder = runs / label
                folder.mkdir()
                (folder / "config.json").write_text(json.dumps({"arm": "D", "source_manifest": {"file.py": manifest}}))
                (folder / "checks.json").write_text('{"status": "completed"}')
            report = aggregate(runs)
            self.assertIn("| 0 | 0 | 0 | 2 | 0 |", report)
            self.assertIn("| 0 | 0 | 0 | 1 | 0 |", report)
            self.assertIn("Claims passing number/date check", report)
            self.assertIn("does not establish factual accuracy", report)


if __name__ == "__main__":
    unittest.main()

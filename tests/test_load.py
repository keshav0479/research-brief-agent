"""Offline document loading tests built from generic source fixtures."""

from datetime import date
from pathlib import Path
import tempfile
import unittest

from brief_agent.load import clean_text, load_documents, sentences, target_profile


def source(body, published="2026-01-10", url="https://ir.example/results", kind="official company filing"):
    return f"---\nsource: Example IR\nurl: {url}\npublished: {published}\ntype: {kind}\n---\n\n{body}\n"


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write(self, name, text):
        (self.directory / name).write_text(text, encoding="utf-8")

    def load(self, as_of=date(2026, 1, 20)):
        return load_documents(self.directory, as_of)

    def test_metadata_age_tier_and_explicit_date(self):
        self.write("a.md", source("# Results\n\nRevenue increased."))
        document = self.load()[0]
        self.assertEqual((document.sid, document.age_days, document.tier), ("S1", 10, 2))
        self.assertEqual(document.source_type, "official company filing")
        self.assertEqual(self.load(date(2026, 1, 11))[0].age_days, 1)
        self.assertEqual(document.title, "Results")
        self.assertEqual(len(document.sha256), 64)

    def test_ids_survive_renaming_and_url_breaks_date_tie(self):
        self.write("z.md", source("# Later\n\nRevenue increased.", url="https://z.example"))
        self.write("a.md", source("# Earlier\n\nCosts increased.", url="https://a.example"))
        before = {doc.sha256: (doc.sid, doc.units) for doc in self.load()}
        (self.directory / "z.md").rename(self.directory / "b.md")
        (self.directory / "a.md").rename(self.directory / "y.md")
        after = {doc.sha256: (doc.sid, doc.units) for doc in self.load()}
        self.assertEqual(before, after)
        self.assertEqual(self.load()[0].url, "https://a.example")

    def test_hash_breaks_identical_metadata_tie(self):
        self.write("x.md", source("One."))
        self.write("y.md", source("Two."))
        self.assertEqual([doc.sha256 for doc in self.load()], sorted(doc.sha256 for doc in self.load()))

    def test_hidden_content_counts_and_normalization(self):
        raw = "Visible<!-- invisible --> text\u200b\ue001\u3164\u2800. ＡＢＣ.<!-- unfinished"
        cleaned, removals = clean_text(raw)
        self.assertEqual(cleaned, "Visible text. ABC.")
        counts = {row["kind"]: row["count"] for row in removals}
        self.assertEqual(counts["html_comment"], len("<!-- invisible --><!-- unfinished"))
        self.assertEqual(counts["unicode_format"], 1)
        self.assertEqual(counts["unicode_private_use"], 1)
        self.assertEqual(counts["unicode_blank"], 2)

    def test_raw_body_preserved_but_hidden_text_never_enters_units(self):
        self.write("a.md", source("# Results\n\nRevenue rose.<!-- hidden text -->\n\nCosts fell."))
        document = self.load()[0]
        self.assertIn("<!-- hidden text -->", document.raw_body)
        for text in [document.body, *document.units.values(), *document.windows.values(), *document.quotes.values()]:
            self.assertNotIn("hidden text", text)
        self.assertTrue(document.removals)

    def test_sentence_abbreviations_and_decimal(self):
        text = "Mr. Arun met Ms. Beena and Dr. Chen at Example Ltd. today. No. 3 is open. Margin was 4.2 percent. Sales rose."
        self.assertEqual(sentences(text), ["Mr. Arun met Ms. Beena and Dr. Chen at Example Ltd. today.",
                                           "No. 3 is open.", "Margin was 4.2 percent.", "Sales rose."])
        self.assertEqual(sentences("Revenue was Rs. 220 crore. Margin rose."),
                         ["Revenue was Rs. 220 crore.", "Margin rose."])

    def test_table_quote_has_caption_and_header(self):
        self.write("a.md", source("# Results\n\n**Consolidated results**\n\n| Metric | Current | Prior |\n|---|---|---|\n| Sales | 120 | 100 |\n| Profit | 12 | 10 |\n\nSales rose."))
        doc = self.load()[0]
        key = next(key for key, unit in doc.units.items() if "| Sales |" in unit)
        quote = doc.quotes[key]
        for expected in ("**Consolidated results**", "| Metric | Current | Prior |", "| Sales | 120 | 100 |"):
            self.assertIn(expected, quote)
        self.assertNotIn("| Profit |", quote)
        self.assertTrue(doc.windows[key].startswith("Results\n"))
        self.assertEqual(len(doc.units), 3)

    def test_prose_window_one_neighbor_and_section_heading(self):
        self.write("a.md", source("# Results\n\n## Trading\n\nFirst result. Second result. Third result. Fourth result.\n\n## Outlook\n\n- Sales may grow. Costs may rise.\n- Demand may soften."))
        doc = self.load()[0]
        second = doc.windows["S1.u02"]
        self.assertIn("Results", second)
        self.assertIn("Trading", second)
        self.assertIn("First result.", second)
        self.assertIn("Third result.", second)
        self.assertNotIn("Fourth result.", second)
        self.assertEqual(doc.units["S1.u05"], "- Sales may grow. Costs may rise.")
        self.assertIn("Outlook", doc.windows["S1.u05"])

    def test_missing_invalid_and_duplicate_metadata_fail_explicitly(self):
        cases = [
            ("# No metadata", "front matter"),
            ("---\nsource: IR\n---\nText", "missing front matter fields"),
            (source("Text").replace("2026-01-10", "2026-02-30"), "ISO date"),
            (source("Text").replace("source: Example IR", "source: One\nsource: Two"), "duplicate"),
        ]
        for content, error in cases:
            with self.subTest(error=error):
                self.write("a.md", content)
                with self.assertRaisesRegex(ValueError, error):
                    self.load()

    def test_unknown_type_and_future_date_logged(self):
        self.write("a.md", source("Text.", published="2027-01-01", kind="interview"))
        doc = self.load()[0]
        self.assertEqual(doc.tier, 3)
        self.assertLess(doc.age_days, 0)
        self.assertEqual(len(doc.warnings), 2)

    def test_quoted_metadata_values(self):
        self.write("a.md", source("Text.").replace("source: Example IR", 'source: "Example IR"'))
        self.assertEqual(self.load()[0].source, "Example IR")

    def test_profile_uses_explicit_identifier_and_business_description(self):
        self.write("a.md", source("# Results\n\n**City, 10 January 2026.** Example Cables Ltd (NSE: EXAMPLE), a maker of wires, cables and conductors, today announced results."))
        self.assertEqual(target_profile(self.load(), "EXAMPLE"), {
            "ticker": "EXAMPLE", "name": "Example Cables Ltd", "business": "a maker of wires, cables and conductors"})
        self.assertEqual(target_profile(self.load(), "UNKNOWN"), {"ticker": "UNKNOWN", "name": "", "business": ""})

    def test_does_not_recurse_and_empty_folder_errors(self):
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / "a.md").write_text(source("Text."))
        with self.assertRaisesRegex(ValueError, "no Markdown"):
            self.load()

    def test_available_research_pack_stays_stable_when_renamed(self):
        pack = Path(__file__).resolve().parents[1] / "research_pack"
        if not pack.is_dir():
            self.skipTest("Optional research pack is not present")
        original = load_documents(pack, date(2026, 9, 23))
        for index, document in enumerate(reversed(original)):
            (self.directory / f"renamed-{index}.md").write_bytes((pack / document.filename).read_bytes())
        renamed = self.load(date(2026, 9, 23))
        self.assertEqual([(d.sha256, d.sid, d.units, d.windows) for d in original],
                         [(d.sha256, d.sid, d.units, d.windows) for d in renamed])


if __name__ == "__main__":
    unittest.main()

"""Offline checks with synthetic evidence, never sealed evaluation data."""

from pathlib import Path
from types import SimpleNamespace
import unittest

from brief_agent.checks import number_errors, validate_claim, wilson, word_count


def document(text="Revenue was Rs 220 crore.", *, age=30, published="2026-08-01", sid="S1", window=None, title='Results'):
    uid = sid + ".u01"
    return SimpleNamespace(sid=sid, age_days=age, published=published, title=title,
                           units={uid: text}, quotes={uid: text},
                           windows={uid: window or text})


def claim(text="Revenue was Rs 220 crore.", *, cites=None, kind="reported_fact"):
    return {"text": text, "cites": ["S1.u01"] if cites is None else cites, "kind": kind}


class NumberTests(unittest.TestCase):
    def test_exact_and_transposed_numbers(self):
        self.assertFalse(number_errors("Revenue ₹1,248 crore.", "Revenue ₹1,248 crore."))
        self.assertTrue(number_errors("Revenue ₹1,428 crore.", "Revenue ₹1,248 crore."))

    def test_approximation_is_explicit_and_uses_displayed_precision(self):
        for wording in ("about 6%", "approximately 6 percent", "~6%"):
            self.assertFalse(number_errors(wording, "+5.9%"), wording)
        self.assertTrue(number_errors("6%", "5.9%"))
        self.assertFalse(number_errors("about 5.9%", "5.86%"))
        self.assertTrue(number_errors("about 5.90%", "5.86%"))
        self.assertTrue(number_errors("about 6%", "5.4%"))

    def test_indian_grouping_and_money_units(self):
        self.assertFalse(number_errors("Rs 1,25,000", "₹125000"))
        self.assertFalse(number_errors("Rs 0.08 crore", "₹8 lakh"))
        self.assertFalse(number_errors("Rs. 800000", "₹8 lakh"))
        self.assertTrue(number_errors("Rs 8 crore", "₹8 lakh"))
        self.assertTrue(number_errors("8%", "₹8 lakh"))

    def test_table_caption_supplies_currency(self):
        evidence = "| ₹ crore | Q1 FY27 | Q1 FY26 | YoY |\n| Revenue | 220 | 200 | +10.0% |"
        self.assertFalse(number_errors("Revenue was ₹220 crore, up 10 percent.", evidence))
        self.assertTrue(number_errors("Revenue was ₹220 lakh.", evidence))

    def test_signed_changes_and_direction_words(self):
        self.assertFalse(number_errors("Margin down 170 basis points.", "Margin −170 bps"))
        self.assertFalse(number_errors("Margin contracted by 170 bps.", "Margin −170 bps"))
        self.assertTrue(number_errors("Margin up 170 bps.", "Margin −170 bps"))
        self.assertTrue(number_errors("−5%", "+5%"))
        self.assertTrue(number_errors("170%", "170 bps"))

    def test_noun_decreases_match_negative_changes(self):
        for noun in ('decrease', 'decline', 'drop', 'fall', 'contraction', 'reduction'):
            text = f'Margin showed a {noun} of 170 basis points.'
            self.assertFalse(number_errors(text, 'Margin change: -170 bps.'), noun)
            self.assertTrue(number_errors(text, 'Margin change: +170 bps.'), noun)

    def test_noun_increases_match_positive_changes(self):
        for noun in ('increase', 'rise', 'growth'):
            text = f'Margin showed an {noun} of 170 basis points.'
            self.assertFalse(number_errors(text, 'Margin change: +170 bps.'), noun)
            self.assertTrue(number_errors(text, 'Margin change: -170 bps.'), noun)

    def test_full_dates_are_atomic_across_supported_formats(self):
        for wording in ('2 September 2026', 'September 2, 2026', '2026-09-02'):
            self.assertFalse(number_errors(f'Published on {wording}.', 'Published 2026-09-02.'))
            errors = number_errors(f'Published on {wording}.', 'Published 2026-09-03.')
            self.assertEqual(len(errors), 1)
            self.assertIn('Date', errors[0])
            self.assertIn('not found in the cited source', errors[0])
        self.assertTrue(number_errors('Published 31 September 2026.', 'Published 2026-09-30.'))
        self.assertTrue(number_errors('There were 2 orders.', 'Published 2 September 2026.'))

    def test_period_labels_and_years_are_ignored(self):
        self.assertFalse(number_errors("Q1 FY27 results compared with H2 FY2026 in 2026.", "Results."))
        self.assertTrue(number_errors("Capacity grew 31% in FY27.", "Capacity grew 30% in FY27."))
        self.assertTrue(number_errors("Revenue was ₹2026 crore.", "Revenue was ₹2025 crore."))

    def test_ranges_and_ratios(self):
        self.assertFalse(number_errors("Management expects 15 to 17 percent growth.", "Guidance is 15-17%."))
        for separator in ("-", " - ", "–", " – ", " to "):
            evidence = f"Guidance is 15{separator}17%."
            self.assertFalse(number_errors("Growth of 15% to 17%.", evidence), evidence)
            self.assertFalse(number_errors(evidence, "Growth of 15% to 17%."), evidence)
        self.assertFalse(number_errors("Debt ratio 0.35 times.", "Net debt to equity is 0.35 times."))
        self.assertTrue(number_errors("Debt ratio 0.35 times.", "Margin 0.35%."))
        self.assertFalse(number_errors("Debt ratio 0.35x.", "Debt ratio 0.35 times."))
        self.assertFalse(number_errors("Debt ratio .35x.", "Debt ratio 0.35 times."))
        self.assertTrue(number_errors("Debt ratio .36x.", "Debt ratio 0.35 times."))

    def test_sentence_end_numbers_cannot_escape_validation(self):
        self.assertTrue(number_errors("Receivable days rose to 97.", "Receivable days rose to 96."))
        self.assertFalse(number_errors("Receivable days rose to 96.", "Receivable days rose to 96."))

    def test_table_units_do_not_leak_into_prose(self):
        evidence = "| ₹ crore | Current | Prior |\n| Revenue | 220 | 200 |\n\nReceivable days rose to 96."
        self.assertFalse(number_errors("Receivable days rose to 96.", evidence))
        self.assertTrue(number_errors("Revenue was ₹96 crore.", evidence))

    def test_table_currency_does_not_leak_to_another_table_or_header_year(self):
        evidence = "| ₹ crore | 2026 | 2025 |\n|---|---|---|\n| Revenue | 220 | 200 |"
        self.assertFalse(number_errors("Revenue was ₹220 crore.", evidence))
        self.assertTrue(number_errors("Revenue was ₹2026 crore.", evidence))
        evidence += "\n\n| Ratio | Current |\n|---|---|\n| Debt | 3 |"
        self.assertTrue(number_errors("Debt was ₹3 crore.", evidence))
        evidence += "\n| ₹ lakh | Current |\n|---|---|\n| Penalty | 8 |"
        self.assertFalse(number_errors("Penalty was ₹8 lakh.", evidence))
        self.assertTrue(number_errors("Penalty was ₹8 crore.", evidence))

    def test_never_recomputes_reported_growth(self):
        evidence = "| ₹ crore | Current | Prior | YoY |\n| EBITDA | 140 | 136 | +2.6% |"
        self.assertFalse(number_errors("EBITDA grew 2.6%.", evidence))
        self.assertTrue(number_errors("EBITDA grew 2.9%.", evidence))


class ClaimTests(unittest.TestCase):
    def test_valid_claim(self):
        self.assertEqual(validate_claim(claim(), [document()], {"S1"}, "snapshot"), [])

    def test_nonempty_valid_evidence_citations_required(self):
        for cites in ([], ["S1.u99"], ["S2.u01"]):
            self.assertTrue(validate_claim(claim(cites=cites), [document()], {"S1"}, "bull"))
        self.assertTrue(validate_claim(claim(), [document()], set(), "bull"))

    def test_numeric_evidence_does_not_leak_from_neighbor_window(self):
        doc = document("The business makes cables.", window="The business makes cables. Revenue was Rs 220 crore.")
        errors = validate_claim(claim(), [doc], {"S1"}, "snapshot")
        self.assertTrue(any("Number" in item for item in errors))

    def test_cited_title_date_and_publication_date_are_supported(self):
        docs = [document('Promoter pledge was 20%.', sid='S2',
                         title='Shareholding as of 30 June 2026', published='2026-07-15'),
                document('The company disclosed a tax demand.', sid='S8',
                         title='Disclosure under Regulation 30', published='2026-09-02')]
        for text, sid in [('As of 30 June 2026, promoter pledge was 20%.', 'S2'),
                          ('The company disclosed a tax demand on September 2, 2026.', 'S8'),
                          ('The company disclosed a tax demand on 2026-09-02.', 'S8')]:
            self.assertFalse(validate_claim(claim(text, cites=[sid + '.u01']), docs, {'S2', 'S8'}, 'bear'))
        wrong = claim('The company disclosed a tax demand on 3 September 2026.', cites=['S8.u01'])
        self.assertEqual(validate_claim(wrong, docs, {'S2', 'S8'}, 'bear'),
                         ['Date 3 September 2026 is not found in the cited source'])
        unrelated = claim('The company disclosed a tax demand on 30 June 2026.', cites=['S8.u01'])
        self.assertTrue(validate_claim(unrelated, docs, {'S2', 'S8'}, 'bear'))
        scalar = claim('The company disclosed 30 demands.', cites=['S8.u01'])
        self.assertTrue(any(error.startswith('Number 30 ') for error in
                            validate_claim(scalar, docs, {'S2', 'S8'}, 'bear')))

    def test_stale_claim_needs_source_month_or_year(self):
        doc = document("Promoter pledge was 20%.", age=700, published="2024-03-18")
        self.assertTrue(validate_claim(claim("Pledge was 20%."), [doc], {"S1"}, "bear"))
        for label in ("2024", "March", "Mar"):
            self.assertFalse(validate_claim(claim(f"In {label}, pledge was 20%."), [doc], {"S1"}, "bear"))
        self.assertTrue(validate_claim(claim("In April, pledge was 20%."), [doc], {"S1"}, "bear"))

    def test_staleness_boundary(self):
        self.assertFalse(validate_claim(claim(), [document(age=365)], {"S1"}, "bull"))
        self.assertTrue(validate_claim(claim(), [document(age=366)], {"S1"}, "bull"))

    def test_advice_uses_words_not_substrings_and_only_claim_prose(self):
        doc = document("Company holding rose. The blog says BUY.")
        for token in ("buy", "sell", "hold", "price target", "upside", "multibagger"):
            errors = validate_claim(claim(f"We recommend {token}."), [doc], {"S1"}, "bull")
            self.assertTrue(any("advice" in error for error in errors), token)
        self.assertFalse(validate_claim(claim("Company holding rose for target_company."), [doc], {"S1"}, "bull"))

    def test_factual_holdings_trade_and_production_targets_are_not_advice(self):
        for text in ('Promoters hold 58.2% of shares.', 'Promoters hold shares.',
                     'The company plans to buy copper and sell cables.',
                     'Management recommends buying raw materials.',
                     'The company will buy ACME shares for a subsidiary.',
                     'Promoters hold ACME shares.', 'The company may sell ACME shares.',
                     'We recommend customers buy spare parts.',
                     'Management set a production target.', 'The target is higher production.'):
            self.assertFalse(validate_claim(claim(text), [document(text)], {'S1'}, 'bull'), text)

    def test_generated_nonprinting_control_character_is_rejected_without_changing_claim(self):
        source = document('Revenue was ₹220 crore.')
        bad = claim('Revenue was \x15220 crore.')
        errors = validate_claim(bad, [source], {'S1'}, 'snapshot')
        self.assertTrue(any('nonprinting control character' in error for error in errors))
        self.assertEqual(bad['text'], 'Revenue was \x15220 crore.')
        self.assertFalse(validate_claim(claim('Revenue was\t₹220 crore.\r\n'),
                                        [source], {'S1'}, 'snapshot'))

    def test_stock_recommendation_phrases_are_blocked(self):
        for text in ('A buy rating.', 'Maintain a "hold" recommendation.', 'A sell call.',
                     'A strong buy.', 'The stock is a buy.',
                     'The rating is buy.', 'The analyst rated it a buy.',
                     'We recommend buying.', 'Investors should hold the shares.',
                     'Buy ACME shares.', 'We recommend investors buy the stock.',
                     'Buy this stock.', 'Sell the shares.', 'Hold now.',
                     'The price target is higher.', 'Potential upside remains.', 'A multibagger.'):
            errors = validate_claim(claim(text), [document(text)], {'S1'}, 'bull')
            self.assertTrue(any('advice' in error for error in errors), text)

    def test_snapshot_cannot_present_a_forecast_as_a_fact(self):
        value = claim("Management expects growth.", kind="management_view_or_forecast")
        doc = document("Management expects growth.")
        self.assertTrue(validate_claim(value, [doc], {"S1"}, "snapshot"))
        self.assertFalse(validate_claim(value, [doc], {"S1"}, "bull"))


class ReportingTests(unittest.TestCase):
    def test_words_exclude_sources(self):
        self.assertEqual(word_count("## Snapshot\nTwo words.\n## Sources\nMany source details here."), 4)

    def test_wilson_returns_counts_and_proportions(self):
        result = wilson(5, 10)
        self.assertEqual((result["k"], result["n"]), (5, 10))
        self.assertAlmostEqual(result["low"], 0.236593, places=5)
        self.assertAlmostEqual(result["high"], 0.763407, places=5)
        self.assertEqual(wilson(0, 0), {"k": 0, "n": 0, "low": None, "high": None})
        with self.assertRaises(ValueError):
            wilson(3, 2)

    def test_production_prompt_hygiene(self):
        root = Path(__file__).resolve().parents[1]
        paths = list((root / 'prompts').glob('*'))
        if not all(path.exists() for path in paths):
            self.skipTest("Production prompts have not been written yet")
        forbidden = ("Sarvottam", "SRVCABLE", "Nagpur", "Bharuch", "Deshpande", "Iyer",
                     "1,248", "1,428", "3,900", "46.3", "STRONG BUY")
        for path in paths:
            content = path.read_text().casefold()
            for value in forbidden:
                self.assertNotIn(value.casefold(), content, str(path.relative_to(root)))
            self.assertNotIn("\N{EM DASH}", content)


if __name__ == "__main__":
    unittest.main()

import unittest

from research_ui.coverage import build_coverage


def source(text, *, sid='S1', uid='S1.u01', quote=None, **changes):
    result = {'id': sid, 'title': 'Example results', 'name': 'Example issuer',
              'published': '2026-06-01', 'tier': 2, 'type': 'official company filing',
              'age_days': 20, 'status': 'evidence',
              'units': [{'id': uid, 'text': text, 'quote': quote or text,
                         'context': 'Neighboring text is not selected evidence.'}]}
    result.update(changes)
    return result


def sections(*texts):
    return [{'key': 'snapshot', 'title': 'Snapshot', 'claims': [
        {'id': f'claim-{index}', 'text': text, 'kind': 'reported_fact', 'cites': ['S1.u01']}
        for index, text in enumerate(texts)]}]


def items(sources, claims):
    return {item['id']: item for item in build_coverage(sources, claims)['items']}


class CoverageTests(unittest.TestCase):
    def test_profit_amount_does_not_cover_growth_even_with_same_row_citation(self):
        row = '| Net profit | 45 | 40 | +12.5% |'
        quote = '**Amounts in crore**\n| Metric | Current | Prior | YoY |\n|---|---|---|---|\n' + row
        result = items([source(row, quote=quote)], sections('Net profit was Rs 45 crore.'))
        self.assertEqual(result['profit_level']['status'], 'mentioned')
        self.assertEqual(result['profit_growth']['status'], 'review_needed')
        self.assertEqual(result['profit_growth']['source_hits'][0]['quote'], quote)
        self.assertEqual(result['profit_growth']['claim_ids'], [])

    def test_growth_mentions_do_not_cover_amounts(self):
        doc = source('Revenue was Rs 500 crore, up 25%.')
        result = items([doc], sections('Revenue grew 25%.'))
        self.assertEqual(result['revenue_growth']['status'], 'mentioned')
        self.assertEqual(result['revenue_level']['status'], 'review_needed')

    def test_absolute_growth_amount_is_not_the_revenue_level(self):
        doc = source('Revenue was Rs 500 crore, up Rs 100 crore.')
        result = items([doc], sections('Revenue growth of Rs 100 crore was reported.'))
        self.assertEqual(result['revenue_growth']['status'], 'mentioned')
        self.assertEqual(result['revenue_level']['status'], 'review_needed')

    def test_signed_table_growth_without_a_header_is_a_candidate(self):
        row = '| Profit | 45 | 40 | +12.5% |'
        result = items([source(row)], sections('Profit rose 12.5% to Rs 45 crore.'))
        self.assertEqual(result['profit_level']['status'], 'mentioned')
        self.assertEqual(result['profit_growth']['status'], 'mentioned')

    def test_growth_of_other_metric_cannot_cover_profit_growth(self):
        doc = source('Profit was Rs 45 crore, up 12.5%.')
        result = items([doc], sections('Profit was Rs 45 crore, while revenue grew 25%.'))
        self.assertEqual(result['profit_growth']['status'], 'review_needed')
        self.assertEqual(result['profit_level']['status'], 'mentioned')

    def test_profit_growth_cannot_cover_revenue_growth(self):
        doc = source('Revenue was Rs 500 crore, up 25%.')
        result = items([doc], sections('Revenue was Rs 500 crore and profit rose 12.5%.'))
        self.assertEqual(result['revenue_growth']['status'], 'review_needed')
        self.assertEqual(result['revenue_level']['status'], 'mentioned')

    def test_forecast_growth_is_outlook_not_reported_growth(self):
        doc = source('Management expects revenue growth of 15% next year.')
        result = items([doc], sections('Revenue grew 15%.'))
        self.assertEqual(result['guidance_outlook']['status'], 'review_needed')
        self.assertEqual(result['revenue_growth']['status'], 'not_found')

    def test_brief_forecast_does_not_cover_reported_profit_growth(self):
        doc = source('Profit increased 12.5% to Rs 45 crore.')
        result = items([doc], sections('Management expects profit to grow 12.5%.'))
        self.assertEqual(result['profit_growth']['status'], 'review_needed')

    def test_margin_percentage_is_not_profit_amount_or_growth(self):
        row = '| EBITDA margin | 14% | 12% | +200 bps |'
        doc = source(row, quote='| Metric | Current | Prior | Change |\n|---|---|---|---|\n' + row)
        result = items([doc], sections('EBITDA margin rose to 14%.'))
        self.assertEqual(result['margins']['status'], 'mentioned')
        self.assertEqual(result['profit_level']['status'], 'not_found')
        self.assertEqual(result['profit_growth']['status'], 'not_found')

    def test_denominator_reference_does_not_imply_revenue_growth(self):
        doc = source('Exports contributed 30% of revenue, up from 20%.')
        result = items([doc], sections('Exports contributed 30% of revenue, up from 20%.'))
        self.assertEqual(result['revenue_growth']['status'], 'not_found')
        self.assertEqual(result['revenue_level']['status'], 'not_found')

    def test_sales_volume_is_not_revenue_value_or_growth(self):
        for text in ('Sales volume increased 20% to 500 units.', '| Sales volume | 500 | 400 | +25% |'):
            with self.subTest(text=text):
                result = items([source(text)], sections(text))
                self.assertEqual(result['revenue_growth']['status'], 'not_found')
                self.assertEqual(result['revenue_level']['status'], 'not_found')

    def test_conditional_growth_is_not_a_reported_result(self):
        text = 'Profit would rise 20% if the expansion succeeds.'
        result = items([source(text)], sections(text))
        self.assertEqual(result['profit_growth']['status'], 'not_found')
        self.assertEqual(result['guidance_outlook']['status'], 'mentioned')

    def test_noneligible_documents_cannot_create_topic_candidates(self):
        docs = [source('Profit was Rs 45 crore.', status='excluded'),
                source('Revenue grew 25%.', status='unclear'),
                source('Borrowings increased.', age_days=-1),
                source('Promoter shares are pledged.', age_days=500),
                source('Management gives guidance.', tier=3),
                source('The company faces litigation.', age_days=None)]
        result = build_coverage(docs, [])
        self.assertTrue(all(item['status'] == 'not_found' for item in result['items']))
        self.assertIn('2 not admitted', result['notice'])
        self.assertIn('1 future', result['notice'])
        self.assertIn('1 older', result['notice'])
        self.assertIn('1 not primary', result['notice'])

    def test_neighbor_context_and_citations_do_not_count_as_selected_text(self):
        doc = source('The business makes equipment.')
        doc['units'][0]['context'] = 'Revenue was Rs 500 crore and profit rose 20%.'
        result = items([doc], sections('The company makes equipment.'))
        self.assertEqual(result['revenue_level']['status'], 'not_found')
        self.assertEqual(result['profit_growth']['status'], 'not_found')

    def test_other_review_topics_link_exact_source_units_and_all_claim_ids(self):
        doc = source('Receivable days increased. Debt funds capital expenditure. Promoters pledged shares. '
                     'Management plans expansion. A tax demand is under legal review.')
        result = items([doc], sections('Working capital needs rose.', 'Receivables take longer to collect.',
                                      'Debt increased.', 'Promoters pledged shares.',
                                      'Management plans expansion.', 'A tax demand is under legal review.'))
        for key in ('cash_collection', 'debt_funding', 'ownership_pledges', 'guidance_outlook', 'material_legal_risk'):
            self.assertEqual(result[key]['status'], 'mentioned', key)
            self.assertEqual(result[key]['source_hits'][0]['unit_id'], 'S1.u01')
        self.assertEqual(result['cash_collection']['claim_ids'], ['claim-0', 'claim-1'])

    def test_reported_and_forecast_clauses_can_match_different_topics(self):
        text = 'Revenue grew 25% and management expects further expansion.'
        result = items([source(text)], sections(text))
        self.assertEqual(result['revenue_growth']['status'], 'mentioned')
        self.assertEqual(result['guidance_outlook']['status'], 'mentioned')

    def test_age_boundary_and_all_matching_source_hits_are_preserved(self):
        docs = [source('Revenue was 500.', age_days=365),
                source('Revenue was 500.', sid='S2', uid='S2.u03', tier=1, age_days=0)]
        result = items(docs, sections('Revenue was 500.'))
        self.assertEqual(result['revenue_level']['status'], 'mentioned')
        self.assertEqual([hit['unit_id'] for hit in result['revenue_level']['source_hits']], ['S1.u01', 'S2.u03'])

    def test_rejected_claims_are_not_brief_mentions(self):
        claims = sections('Revenue was Rs 500 crore.')
        claims[0]['claims'][0]['accepted'] = False
        result = items([source('Revenue was Rs 500 crore.')], claims)
        self.assertEqual(result['revenue_level']['status'], 'review_needed')

    def test_uncited_passage_with_unstated_figures_makes_topic_partial(self):
        docs = [source('Management expects revenue growth of 15 to 17 percent for FY27.', uid='S1.u01'),
                source('The company says the new plant is on track to begin production.', uid='S1.u02')]
        claims = sections('The company says the new plant is on track to begin production.')
        claims[0]['claims'][0]['cites'] = ['S1.u02']
        result = items(docs, claims)
        guidance = result['guidance_outlook']
        self.assertEqual(guidance['status'], 'partial')
        self.assertEqual(guidance['missing_figures'], ['15', '17'])
        self.assertEqual([hit['cited'] for hit in guidance['source_hits']], [False, True])

    def test_uncited_passage_whose_figures_are_stated_stays_mentioned(self):
        docs = [source('Revenue was Rs 500 crore in Q1 FY27 on 30 June 2026.', uid='S1.u01'),
                source('Revenue was Rs 500 crore.', sid='S2', uid='S2.u01')]
        result = items(docs, sections('Revenue was Rs 500 crore.'))
        self.assertEqual(result['revenue_level']['status'], 'mentioned')
        self.assertEqual(result['revenue_level']['missing_figures'], [])

    def test_compact_period_labels_do_not_create_missing_financial_figures(self):
        for period in ('Q1FY27', 'H1FY2026', 'q4fy2026-27', 'Q1 FY27', 'FY27', 'Q1', 'H2'):
            with self.subTest(period=period):
                docs = [source(f'Revenue was Rs 500 crore in {period}.', uid='S1.u01'),
                        source('Revenue was Rs 500 crore.', sid='S2', uid='S2.u01')]
                claims = sections('Revenue was Rs 500 crore.')
                claims[0]['claims'][0]['cites'] = ['S2.u01']
                result = items(docs, claims)['revenue_level']
                self.assertEqual(result['status'], 'mentioned')
                self.assertEqual(result['missing_figures'], [])

    def test_date_case_does_not_create_missing_figures_but_actual_amounts_remain(self):
        for publication in ('30 JUNE 2026', 'JUNE 30, 2026', '30 june 2026', 'June 30, 2026', '2026-06-30'):
            for amount in (500, 600):
                with self.subTest(publication=publication, amount=amount):
                    docs = [source(f'Revenue was Rs {amount} crore on {publication}.', uid='S1.u01'),
                            source('Revenue was Rs 500 crore.', sid='S2', uid='S2.u01')]
                    claims = sections('Revenue was Rs 500 crore.')
                    claims[0]['claims'][0]['cites'] = ['S2.u01']
                    result = items(docs, claims)['revenue_level']
                    self.assertEqual(result['status'], 'mentioned' if amount == 500 else 'partial')
                    self.assertEqual(result['missing_figures'], [] if amount == 500 else ['600'])

    def test_absence_is_explicitly_not_a_completeness_or_truth_judgment(self):
        result = build_coverage([], sections('Profit rose 12.5%.'))
        self.assertEqual(len(result['items']), 10)
        self.assertIn('not completeness, factual accuracy or materiality', result['notice'])
        self.assertIn('Older unresolved issues', result['notice'])
        growth = next(item for item in result['items'] if item['id'] == 'profit_growth')
        self.assertEqual(growth['status'], 'not_found')
        self.assertEqual(growth['claim_ids'], ['claim-0'])
        self.assertIn('Brief wording did match', growth['note'])


if __name__ == '__main__':
    unittest.main()

import unittest

from brief_agent.render import render
from brief_agent.writer import SECTIONS


class RenderTests(unittest.TestCase):
    def test_glossary_defines_only_terms_in_accepted_claims_and_keeps_five_sections(self):
        data = {section: [] for section in SECTIONS}
        data['snapshot'] = [{'text': 'EBITDA margin fell 20 bps; the order book is 12 crore.',
                             'cites': ['S1.u01']}]
        brief = render(data, [], {}, 'DEMO', '2026-09-23')
        self.assertIn('EBITDA margin: earnings before interest', brief)
        self.assertIn('100 basis points equal one percentage point', brief)
        self.assertIn('Order book: orders received but not yet completed', brief)
        self.assertIn('Crore: 10 million', brief)
        self.assertNotIn('Receivable days:', brief)
        self.assertNotIn('Pledged shares:', brief)
        self.assertNotIn('Price-variation clause:', brief)
        self.assertEqual(brief.count('\n## '), 5)
        self.assertGreater(brief.index('Terms used'), brief.index('## Sources'))
        data['snapshot'] = []
        self.assertNotIn('Terms used', render(data, [], {}, 'DEMO', '2026-09-23'))


if __name__ == '__main__':
    unittest.main()

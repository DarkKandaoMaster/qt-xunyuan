import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TopicLibraryTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / 'web/topic-library.html').read_text(encoding='utf-8')
        self.units = json.loads(re.search(r'const units = (.*);\nconst buckets=', self.html).group(1))

    def test_all_document_units_have_activity_queries(self):
        rules = json.loads((ROOT / 'rules/qt_rules_v4.yaml').read_text(encoding='utf-8'))
        self.assertEqual({u['id'] for u in self.units}, set(rules['units']))
        self.assertEqual(len(self.units), 53)
        for unit in self.units:
            self.assertGreaterEqual(len(unit['topics']), 6)
            for key in ('find', 'pass', 'exclude', 'page', 'target'):
                self.assertEqual(unit[key], rules['units'][unit['id']][key])
            for topic in unit['topics']:
                self.assertTrue(all(topic[k].strip() for k in ('label', 'query', 'check')))

    def test_queries_are_unique_and_page_is_portable(self):
        queries = [t['query'] for u in self.units for t in u['topics']]
        self.assertEqual(len(queries), 334)
        self.assertEqual(len(set(queries)), len(queries))
        self.assertNotIn('<script src=', self.html)
        self.assertNotIn('<link rel="stylesheet"', self.html)
        self.assertNotIn('/* TOPICS_DATA */', self.html)

    def test_workbench_prefill_does_not_submit_search(self):
        js = (ROOT / 'web/dashboard.js').read_text(encoding='utf-8')
        block = js.split("const topicParams =", 1)[1].split('const bucketOptions', 1)[0]
        self.assertIn(".value = topicParams.get('topic_query')", block)
        self.assertNotIn('submit(', block)
        self.assertNotIn('fetch(', block)
        self.assertNotIn('api(', block)


if __name__ == '__main__':
    unittest.main()

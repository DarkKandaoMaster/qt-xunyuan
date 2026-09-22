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
        self.assertGreaterEqual(len(queries), 350)
        self.assertEqual(len(set(queries)), len(queries))
        self.assertNotIn('<script src=', self.html)
        self.assertNotIn('<link rel="stylesheet"', self.html)
        self.assertNotIn('/* TOPICS_DATA */', self.html)

    def test_only_third_person_positive_searches_and_synced_templates(self):
        templates = json.loads((ROOT / 'rules/search_templates.yaml').read_text(encoding='utf-8'))
        self.assertEqual(templates['collection_scope']['viewpoint'], 'third_person')
        self.assertEqual(set(templates['templates']), {u['id'] for u in self.units})
        forbidden = re.compile(
            r'\b(?:pov|fpv|first[ -]+person|point[ -]+of[ -]+view|cockpit|'
            r'on[ -]?board|dash[ -]?cam|body[ -]?cam|helmet[ -]?cam|'
            r'head[ -]?(?:cam|mounted)|chest[ -]?(?:cam|mounted)|selfie)\b', re.I
        )
        for unit in self.units:
            queries = [topic['query'] for topic in unit['topics']]
            self.assertEqual(templates['templates'][unit['id']]['positive'], queries)
            for query in queries:
                self.assertIsNone(forbidden.search(query), (unit['id'], query))
            self.assertNotIn('第一人称为主', unit['current_pass'])
            self.assertTrue(unit['current_find'])

    def test_curated_source_and_generated_page_are_current(self):
        source_units = {}
        for line in (ROOT / 'docs/activity_topics.txt').read_text(encoding='utf-8').splitlines():
            if not line or line.startswith('#'):
                continue
            if line.startswith('@'):
                key, priority, note = line[1:].split('|')
                source_units[key] = {'priority': priority, 'note': note, 'topics': []}
            else:
                label, query, check = line.split('|')
                source_units[key]['topics'].append(dict(label=label, query=query, check=check))
        for unit in self.units:
            for field in ('priority', 'note', 'topics'):
                self.assertEqual(unit[field], source_units[unit['id']][field])
        template = (ROOT / 'scripts/topic_library_template.html').read_text(encoding='utf-8')
        data = json.dumps(self.units, ensure_ascii=False).replace('<', '\\u003c')
        self.assertEqual(self.html, template.replace('/* TOPICS_DATA */[]', data))

    def test_scope_is_visible_and_historical_quotas_are_not_live_demand(self):
        self.assertIn('客户最新口径：当前仅采集第三人称', self.html)
        self.assertIn('历史规划（非当前配额）', self.html)
        self.assertIn('旧版9月20日、21日关键词文档中的第一人称方向已停用', self.html)
        self.assertNotIn('T3/T4重点补第一人称', self.html)
        self.assertIn('esc(u.current_find)', self.html)
        self.assertIn('esc(u.current_pass)', self.html)

    def test_workbench_prefill_does_not_submit_search(self):
        js = (ROOT / 'web/dashboard.js').read_text(encoding='utf-8')
        block = js.split("const topicParams =", 1)[1].split('const bucketOptions', 1)[0]
        self.assertIn(".value = topicParams.get('topic_query')", block)
        self.assertNotIn('submit(', block)
        self.assertNotIn('fetch(', block)
        self.assertNotIn('api(', block)


if __name__ == '__main__':
    unittest.main()

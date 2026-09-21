"""Build a portable, offline topic library from curated activities and local rules."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rules = json.loads((ROOT / 'rules/qt_rules_v4.yaml').read_text(encoding='utf-8'))
units = []
for line in (ROOT / 'docs/activity_topics.txt').read_text(encoding='utf-8').splitlines():
    if not line or line.startswith('#'):
        continue
    if line.startswith('@'):
        key, priority, note = line[1:].split('|')
        rule = rules['units'][key]
        units.append(dict(id=key, priority=priority, note=note, **rule, topics=[]))
    else:
        label, query, check = line.split('|')
        units[-1]['topics'].append(dict(label=label, query=query, check=check))
assert {u['id'] for u in units} == set(rules['units'])
assert len(units) == 53
assert all(len(u['topics']) >= 6 for u in units)
queries = [t['query'] for u in units for t in u['topics']]
assert len(set(queries)) == len(queries), 'Duplicate search query'
data = json.dumps(units, ensure_ascii=False).replace('<', '\\u003c')
template = (ROOT / 'scripts/topic_library_template.html').read_text(encoding='utf-8')
output = ROOT / 'web/topic-library.html'
output.write_text(template.replace('/* TOPICS_DATA */[]', data), encoding='utf-8')
print(f'{len(units)} units, {len(queries)} topics -> {output}')

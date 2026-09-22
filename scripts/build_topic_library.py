"""Build a portable, offline topic library from curated activities and local rules."""
import json
import re
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
# The original document remains available verbatim; these are the current
# sourcing directions after applying the customer's third-person-only scope.
current_find = {
    'T1.7': '外部摄影跟随直升机、轻型飞机、滑翔机等空中载具；不采驾驶舱视野',
    'T1.8': '外部跟随船、摩托艇、皮划艇等水上载具；不采驾驶位或纯船头视野',
    'T6.3': '飞行摄影跟随可辨人物、动物或载具探索空间；不采无主体的主观飞行画面',
}
for unit in units:
    unit['current_find'] = current_find.get(unit['id'], unit['find'])
    unit['current_pass'] = (
        '当前仅第三人称：外部拍摄实际操作者，看到手操作，动作完整'
        if unit['id'] == 'T4.1' else unit['pass']
    )
queries = [t['query'] for u in units for t in u['topics']]
assert len(set(queries)) == len(queries), 'Duplicate search query'
# Positive searches must not suggest first-person capture. Mentions in original
# rule text or in exclusion warnings are intentionally preserved for provenance.
first_person_query = re.compile(
    r'\b(?:pov|fpv|first[\s-]+person|point[\s-]+of[\s-]+view|'
    r'cockpit|on[\s-]?board|dash[\s-]?cam|body[\s-]?cam|helmet[\s-]?cam|'
    r'head[\s-]?(?:cam|mounted)|chest[\s-]?(?:cam|mounted)|selfie)\b', re.I
)
assert not any(first_person_query.search(q) for q in queries), 'First-person search query'

# Keep both keyword entry points in sync; do not modify the historical QA rules
# or reinterpret first-person quotas as new third-person targets.
search_templates = {
    'version': 4,
    'collection_scope': {
        'updated': '2026-09-22',
        'viewpoint': 'third_person',
        'note': '客户当前不采集第一人称；搜索词是待人工筛选线索，不保证结果人称或质量。',
    },
    'defaults': {
        'negative': ['montage', 'compilation', 'shorts', 'gameplay', 'reaction', 'subtitles', 'lyrics'],
    },
    'templates': {u['id']: {'positive': [t['query'] for t in u['topics']]} for u in units},
}
data = json.dumps(units, ensure_ascii=False).replace('<', '\\u003c')
template = (ROOT / 'scripts/topic_library_template.html').read_text(encoding='utf-8')
output = ROOT / 'web/topic-library.html'
output.write_text(template.replace('/* TOPICS_DATA */[]', data), encoding='utf-8')
(ROOT / 'rules/search_templates.yaml').write_text(
    json.dumps(search_templates, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
)
print(f'{len(units)} units, {len(queries)} topics -> {output}')

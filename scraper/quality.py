"""Shared query/airline quality reporting; never substitutes prices or schedules."""
import json
import os
from collections import Counter
from pathlib import Path

LABELS = {
    'ok': '有实时往返报价', 'partial': '部分查询失败，报价覆盖不完整', 'no_flights': '数据源明确返回空航班列表（不代表航司停飞）',
    'unpriced': '有航班但无报价', 'parse_error': '解析失败',
    'source_error': '数据源异常', 'unknown_empty': '空结果，旧记录无法确认原因',
    'not_scanned': '缺少查询记录', 'no_combinations': '有航班，但无法组成可报价的往返',
}


def query_status(count, priced, error=None):
    if error:
        return 'source_error' if error.startswith('SourceError:') else 'parse_error'
    return 'no_flights' if not count else 'unpriced' if not priced else 'ok'


def summarize(route, coverage, rows, only=()):
    result = {}
    for code, name in route['direct_airlines'].items():
        if only and code not in only:
            continue
        queries = [c for c in coverage if c['route'] == route['id'] and c['code'] == code]
        states = Counter(c.get('status') or ('parse_error' if c['error'] else
                         'unknown_empty' if not c['count'] else
                         'unpriced' if not c.get('priced', c['count']) else 'ok') for c in queries)
        combos = sum(r['o']['c'] == code and r['r']['c'] == code and not r.get('old') for r in rows)
        if combos:
            status = 'partial' if states['source_error'] or states['parse_error'] else 'ok'
        elif not queries:
            status = 'not_scanned'
        else:
            status = next((s for s in ('source_error', 'parse_error', 'unknown_empty', 'unpriced') if states[s]),
                          'no_flights' if states['no_flights'] == len(queries) else 'no_combinations')
        result[code] = {'airline': name, 'status': status, 'message': LABELS[status],
                        'queries': len(queries), 'states': dict(states), 'combos': combos}
    return result


def check(raw_path):
    # Imported here so scan.py can use query_status without importing the builder.
    import yaml
    from build import ROOT, combine
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text())
    raw = json.loads(Path(raw_path).read_text())
    report = {}
    for route in cfg['routes']:
        if raw.get('only') and not set(raw['only']) & set(route['direct_airlines']):
            continue
        rr = raw['routes'].get(route['id'], {'outbound': [], 'inbound': []})
        rows = [r for r in combine(rr, cfg, cfg['sakura']['full_bloom']) if r['n'] in cfg['core_nights']]
        report[route['id']] = summarize(route, raw.get('coverage', []), rows, raw.get('only', []))
    lines = ['## Flight data quality', '', '| Route | Airline | Result | Queries | Fresh combinations |',
             '| --- | --- | --- | --- | --- |']
    missing = []
    for rid, airlines in report.items():
        for code, q in airlines.items():
            lines.append(f"| {rid} | {q['airline']} | {q['message']} | {q['queries']} | {q['combos']} |")
            if q['status'] != 'ok':
                missing.append(f"{rid} {code}: {q['message']}")
    Path(raw_path).with_suffix('.quality.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('\n'.join(lines) + '\n')
    for msg in missing:
        print(f'::error::{msg}')
    for err in raw.get('errors', []):
        print(f'::warning::{err}')
    print(f"Data quality: {len(missing)} airlines without fresh round-trip quotes; {len(raw.get('errors', []))} query errors")
    return bool(missing or raw.get('errors'))


if __name__ == '__main__':
    import sys
    sys.exit(check(sys.argv[1]))

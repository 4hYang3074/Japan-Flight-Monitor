"""一次性探索：不限樱花季，扫描未来约 11 个月 KUL↔KIX 直飞单程最低价，找出最便宜的出发时间。
用法：python scraper/explore.py scan <块序号> <块数>   扫描其中一段日期，写 data/explore_<块序号>.json
      python scraper/explore.py report                合并各段结果，输出最便宜的往返组合"""
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan import SLEEP, daterange, fetch, to_record  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DAYS = 330  # Google Flights 一般只开放约 11 个月内的航班
ORIGIN, DEST = "KUL", "KIX"


def scan(part, parts):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    first = date.today() + timedelta(days=1)
    days = list(daterange(first, first + timedelta(days=DAYS - 1)))
    size = -(-len(days) // parts)
    chunk = days[part * size:(part + 1) * size]
    out, errors = [], []
    for d in chunk:
        for frm, to in ((ORIGIN, DEST), (DEST, ORIGIN)):
            flights, err = fetch(frm, to, d, cfg["currency"], max_stops=0)
            if err:
                errors.append(f"{frm}->{to} {d}: {err}")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            out += [to_record(f, cfg["airport_utc_offset"], now, "explore")
                    for f in flights if len(f["legs"]) == 1 and f["price"] is not None]
            time.sleep(SLEEP)
        print(f"{d} done ({len(out)} priced flights, {len(errors)} errors)", flush=True)
    DATA.mkdir(exist_ok=True)
    (DATA / f"explore_{part}.json").write_text(json.dumps({"flights": out, "errors": errors}, ensure_ascii=False))


def report():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    flights, errors = [], []
    for p in sorted(DATA.glob("explore_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        flights += d["flights"]
        errors += d["errors"]
    best = {}  # (方向, 日期) -> 当天最便宜的直飞
    for f in flights:
        k = (f["from"], f["dep"][:10])
        if k not in best or f["price"] < best[k]["price"]:
            best[k] = f
    combos = []
    for (frm, day), o in best.items():
        if frm != ORIGIN:
            continue
        for n in cfg["nights"]:
            r = best.get((DEST, (date.fromisoformat(day) + timedelta(days=n)).isoformat()))
            if r:
                combos.append({"p": o["price"] + r["price"], "d": day, "n": n, "o": o, "r": r})
    combos.sort(key=lambda c: c["p"])

    def line(c):
        return (f"| RM{c['p']:,} | {c['d']} | {c['n']} | {c['o']['airline']} {c['o']['dep'][11:]} (RM{c['o']['price']:,}) "
                f"| {c['r']['airline']} {c['r']['dep'][5:]} (RM{c['r']['price']:,}) |")

    head = "| 每人往返 | 去程日期 | 晚数 | 去程 | 回程 |\n|---|---|---|---|---|"
    lines = [f"# KUL↔KIX 直飞：未来 {DAYS} 天最便宜往返（{cfg['currency']}，每人经济舱，Google Flights 最低票价档）", "",
             f"扫描到 {len(best)} 个方向×日期有报价，{len(errors)} 个查询失败；晚数 {cfg['nights']}。", "",
             "## 全期最便宜 15 个组合", "", head] + [line(c) for c in combos[:15]]
    by_month = defaultdict(list)
    for c in combos:
        by_month[c["d"][:7]].append(c)
    lines += ["", "## 每个月最便宜的组合", "", head] + [line(by_month[m][0]) for m in sorted(by_month)]
    for frm, label in ((ORIGIN, "去程 KUL→KIX"), (DEST, "回程 KIX→KUL")):
        one = sorted((f for (x, _), f in best.items() if x == frm), key=lambda f: f["price"])[:5]
        lines += ["", f"## {label} 单程最低 5 天", ""] + [f"- RM{f['price']:,} · {f['dep']} · {f['airline']}" for f in one]
    if errors:
        lines += ["", "## 失败的查询（前 10 个）", ""] + [f"- {e}" for e in errors[:10]]
    text = "\n".join(lines) + "\n"
    (DATA / "explore_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    if sys.argv[1] == "scan":
        scan(int(sys.argv[2]), int(sys.argv[3]))
    else:
        report()

"""加扫时段：把 data/raw_watch.json 与当天早上例行扫描（docs/data.json）的同一组合比价，
结果累积到 docs/watch.json；樱花期明显更便宜时写 data/alert.md 触发通知。"""
import json
from datetime import datetime, timedelta, timezone

import yaml

from build import DOCS, ROOT, combine, load, row_key

DROP_PCT = 5
KEEP = 300
WEEKDAY = "一二三四五六日"


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw = load(ROOT / "data" / "raw_watch.json")
    daily = load(DOCS / "data.json", {})
    watch = load(DOCS / "watch.json", [])
    bloom = cfg["sakura"]["full_bloom"]
    at = datetime.fromisoformat(raw["scanned_at"]).astimezone(timezone(timedelta(hours=8)))
    slot = f"周{WEEKDAY[at.weekday()]} {at:%H:%M}"

    alerted = {tuple(k) for w in watch for k in w.get("alerted", [])}
    daily_routes = {r["id"]: r for r in daily.get("routes", [])}
    alert_lines, entry_routes = [], []
    for rid, rr in raw["routes"].items():
        base = {}
        for r in daily_routes.get(rid, {}).get("rows", []):
            for d in r["dates"]:
                base[(d,) + row_key(r)[1:]] = r["p"]
        rows = [r for r in combine(rr, cfg, bloom) if r["n"] in cfg["core_nights"]]
        diffs, drops = [], []
        for r in rows:
            old = base.get(row_key(r))
            if old is None:
                continue
            pct = (r["p"] - old) / old * 100
            diffs.append(pct)
            if pct <= -DROP_PCT:
                drops.append({**r, "was": old, "pct": round(pct, 1)})
        drops.sort(key=lambda r: r["pct"])
        cheapest = min(rows, key=lambda r: r["p"], default=None)
        entry_routes.append({
            "route": rid, "combos": len(rows), "compared": len(diffs),
            "cheaper": sum(d < -0.5 for d in diffs), "pricier": sum(d > 0.5 for d in diffs),
            "avg_diff_pct": round(sum(diffs) / len(diffs), 2) if diffs else None,
            "cheapest": {"p": cheapest["p"], "od": cheapest["od"], "n": cheapest["n"], "dep": cheapest["o"]["dep"]} if cheapest else None,
            "best_drop": {k: drops[0][k] for k in ("p", "was", "pct", "od", "n")} if drops else None,
        })
        for r in drops:
            if r["sk"] not in ("核心窗口", "接近核心"):
                continue
            key = (rid,) + row_key(r) + (r["p"],)
            if key in alerted:
                continue
            alerted.add(key)
            alert_lines.append(
                f"- **RM{r['p']:,}**（早上例行扫描是 RM{r['was']:,}，便宜 {-r['pct']}%）· {r['od'][5:].replace('-', '/')} 出发 "
                f"{r['n']}晚 · {r['o']['al']}（去 {r['o']['dep']}，回 {r['r']['dep']}）")
            entry_routes[-1].setdefault("alerted", []).append(list(key))

    entry = {"at": raw["scanned_at"], "slot": slot, "only": raw.get("only", []),
             "errors": len(raw["errors"]), "routes": entry_routes,
             "alerted": [k for e in entry_routes for k in e.pop("alerted", [])]}
    watch = (watch + [entry])[-KEEP:]
    (DOCS / "watch.json").write_text(json.dumps(watch, ensure_ascii=False, indent=0), encoding="utf-8")

    alert = ROOT / "data" / "alert.md"
    alert.unlink(missing_ok=True)
    if alert_lines:
        alert.write_text(f"{slot} 加扫 AirAsia X，发现比今天早上更便宜的樱花期机票（每人经济舱往返）：\n\n"
                         + "\n".join(alert_lines[:15])
                         + "\n\n网页：https://4hyang3074.github.io/Japan-Flight-Monitor/\n\n"
                         "价格是扫描当时 Google Flights 的报价，订票时以航司结账页为准。\n", encoding="utf-8")
    print(f"watch {slot}: " + ", ".join(f"{e['route']} compared={e['compared']} cheaper={e['cheaper']} "
                                         f"avg={e['avg_diff_pct']}%" for e in entry_routes)
          + f", alerts={len(alert_lines)}")


if __name__ == "__main__":
    main()

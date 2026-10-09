"""补价：Google Flights 有航班但没给价格时（2026-10 起 AirAsia X、Batik 都这样），
用 Travelpayouts（Aviasales）Data API 的缓存价补上同航司、同日、同起飞时间的直飞单程价。
用法：python scraper/tp_fill.py [raw.json|raw_watch.json]   需要环境变量 TRAVELPAYOUTS_TOKEN，没有就跳过。
价格来自 Aviasales 用户最近 2–7 天搜索到的缓存，不是实时报价。"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def call(token, **params):
    req = urllib.request.Request(f"{API}?{urlencode(params)}", headers={"X-Access-Token": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(token, frm, to, months, currency):
    """返回 {(航司, 日期): [(起飞时间 HH:MM, 价格)]}，只要直飞单程。"""
    out = defaultdict(list)
    for m in sorted(months):
        d = call(token, origin=frm, destination=to, departure_at=m, one_way="true", direct="true",
                 currency=currency.lower(), market="my", sorting="price", limit=1000)
        for t in d.get("data") or []:
            dep = t.get("departure_at") or ""
            if t.get("transfers", 0) == 0 and t.get("airline") and len(dep) >= 16 and t.get("price") is not None:
                out[(t["airline"], dep[:10])].append((dep[11:16], round(t["price"])))
        time.sleep(0.5)
    return out


def fill(raw, token, currency):
    filled, now = 0, datetime.now(timezone.utc).isoformat(timespec="seconds")
    for rid, r in raw["routes"].items():
        for key in ("outbound", "inbound"):
            missing = [x for x in r.get(key, []) if x["price"] is None and x["stops"] == 0]
            if not missing:
                continue
            frm, to = missing[0]["from"], missing[0]["to"]
            tp = fetch(token, frm, to, {x["dep"][:7] for x in missing}, currency)
            for x in missing:
                cands = tp.get((x["code"], x["dep"][:10]), [])
                # 同一航司当天有多班时只认起飞时间一致的；只有一班报价且当天也只有这一班时才放宽
                same_time = [p for t, p in cands if t == x["dep"][11:16]]
                if same_time:
                    price = min(same_time)
                elif len(cands) == 1 and sum(1 for y in r[key] if y["code"] == x["code"] and y["dep"][:10] == x["dep"][:10]) == 1:
                    price = cands[0][1]
                else:
                    continue
                x.update(price=price, source="travelpayouts", fetched_at=now)
                filled += 1
                # 让 build.py 把这家航司当天视为“有报价”，不再走旧价逻辑
                for c in raw.get("coverage", []):
                    if (c["route"], c["from"], c["date"], c["code"]) == (rid, x["from"], x["dep"][:10], x["code"]):
                        c["priced"] = min(c["count"], c.get("priced", 0) + 1)
            print(f"{rid} {key}: {len(missing)} 班缺价，补上 {sum(1 for x in missing if x['price'] is not None)} 班", flush=True)
    return filled


def main():
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    path = ROOT / "data" / (sys.argv[1] if len(sys.argv) > 1 else "raw.json")
    if not token or not path.exists():
        print("tp_fill: 没有 TRAVELPAYOUTS_TOKEN 或扫描结果，跳过")
        return
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        n = fill(raw, token, cfg["currency"])
    except Exception as e:
        # 补价失败不影响原本的 Google Flights 结果
        raw.setdefault("errors", []).append(f"travelpayouts: {type(e).__name__}: {e}")
        n = 0
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"tp_fill: 共补上 {n} 班价格")


if __name__ == "__main__":
    main()

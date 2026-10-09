"""临时诊断（第三轮）：Travelpayouts（Aviasales）Data API 能否提供 KUL↔KIX 直飞（含 AirAsia X / Batik）的价格。
需要仓库 Secret TRAVELPAYOUTS_TOKEN。"""
import json
import os
import sys
from collections import Counter
from datetime import date

import urllib.request
from urllib.parse import urlencode

TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN")
if not TOKEN:
    sys.exit("没有 TRAVELPAYOUTS_TOKEN")


def call(**params):
    url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + urlencode(params)
    req = urllib.request.Request(url, headers={"X-Access-Token": TOKEN, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


today = date.today()
months = [f"{today.year + (today.month - 1 + i) // 12}-{(today.month - 1 + i) % 12 + 1:02d}" for i in range(12)]
for frm, to in (("KUL", "KIX"), ("KIX", "KUL")):
    total, airlines = 0, Counter()
    for m in months:
        try:
            d = call(origin=frm, destination=to, departure_at=m, one_way="true", direct="true",
                     currency="myr", market="my", sorting="price", limit=1000)
        except Exception as e:
            print("ERROR", frm, to, m, type(e).__name__, str(e)[:200], flush=True)
            continue
        rows = d.get("data", [])
        total += len(rows)
        airlines.update(r.get("airline") for r in rows)
        cheapest = rows[0] if rows else None
        print("MONTH", frm, to, m, "rows", len(rows), "success", d.get("success"), "currency", d.get("currency"),
              "cheapest", json.dumps(cheapest, ensure_ascii=False), flush=True)
    print("TOTAL", frm, to, total, dict(airlines), flush=True)

d = call(origin="KUL", destination="KIX", departure_at=months[1], one_way="true", direct="true",
         currency="myr", market="my", sorting="price", limit=5)
print("SAMPLE", json.dumps(d, ensure_ascii=False)[:2000])

# 用 Travelpayouts 的每日直飞单程最低价，组合出 6/7/8 晚往返（去回可不同航司）
from collections import defaultdict
from datetime import timedelta
best = {}
for frm, to in (("KUL", "KIX"), ("KIX", "KUL")):
    for m in months:
        try:
            rows = call(origin=frm, destination=to, departure_at=m, one_way="true", direct="true",
                        currency="myr", market="my", sorting="price", limit=1000).get("data", [])
        except Exception:
            continue
        for r in rows:
            k = (frm, r["departure_at"][:10])
            if r.get("transfers", 0) == 0 and (k not in best or r["price"] < best[k]["price"]):
                best[k] = r
combos = []
for (frm, day), o in best.items():
    if frm != "KUL":
        continue
    for n in (6, 7, 8):
        r = best.get(("KIX", (date.fromisoformat(day) + timedelta(days=n)).isoformat()))
        if r:
            combos.append((o["price"] + r["price"], day, n, o, r))
combos.sort(key=lambda c: c[0])
fmt = lambda c: f"RM{c[0]:,.0f} | 去 {c[1]} {c[3]['airline']}{c[3].get('flight_number','')} {c[3]['departure_at'][11:16]} RM{c[3]['price']:,.0f} | 回 {c[4]['departure_at'][:10]} {c[4]['airline']}{c[4].get('flight_number','')} {c[4]['departure_at'][11:16]} RM{c[4]['price']:,.0f} | {c[2]}晚"
print("COMBOS", len(combos), "days KUL", sum(1 for k in best if k[0] == "KUL"), "days KIX", sum(1 for k in best if k[0] == "KIX"))
for c in combos[:15]:
    print("TOP", fmt(c))
by_m = defaultdict(list)
for c in combos:
    by_m[c[1][:7]].append(c)
for m in sorted(by_m):
    print("MONTHBEST", m, fmt(by_m[m][0]))

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

"""临时诊断：Google Flights 从 10/7 起不给 AirAsia X / Batik 报价，测试哪些查询方式在 GitHub Actions 上还能拿到价格。"""
import json
import sys
from collections import Counter
from pathlib import Path

from fast_flights import FlightQuery, create_query
from primp import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan import parse  # noqa: E402

DATES = ["2026-11-10", "2027-01-15", "2027-04-05"]


def client():
    return Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)


def google(d, extra, airlines=None, frm="KUL", to="KIX"):
    q = create_query(flights=[FlightQuery(date=d, from_airport=frm, to_airport=to, airlines=airlines, max_stops=0)],
                     trip="one-way", currency="MYR", language="en")
    html = client().get("https://www.google.com/travel/flights", params=q.params() | extra).text
    fl = parse(html)
    seen, priced = Counter(), Counter()
    for f in fl:
        k = "/".join(f["airlines"])
        seen[k] += 1
        if f["price"] is not None:
            priced[k] += 1
    cheapest = {}
    for f in fl:
        k = "/".join(f["airlines"])
        if f["price"] is not None and (k not in cheapest or f["price"] < cheapest[k]):
            cheapest[k] = f["price"]
    return {"seen": dict(seen), "priced": dict(priced), "cheapest": cheapest}


VARIANTS = {"default": {}, "gl=MY": {"gl": "MY"}, "gl=MY hl=en-GB": {"gl": "MY", "hl": "en-GB"},
            "gl=SG": {"gl": "SG"}, "gl=JP": {"gl": "JP"}}

for d in DATES:
    for name, extra in VARIANTS.items():
        try:
            print("GOOGLE", d, f"[{name}]", json.dumps(google(d, extra), ensure_ascii=False), flush=True)
        except Exception as e:
            print("GOOGLE", d, f"[{name}]", "ERROR", type(e).__name__, str(e)[:200], flush=True)
    for name in ("default", "gl=MY"):
        for code in ("D7", "OD"):
            try:
                print("GOOGLE", d, f"[{name} airline={code}]", json.dumps(google(d, VARIANTS[name], [code]), ensure_ascii=False), flush=True)
            except Exception as e:
                print("GOOGLE", d, f"[{name} airline={code}]", "ERROR", type(e).__name__, str(e)[:200], flush=True)

URLS = [
    "https://k.airasia.com/availabledates/api/v1/pricecalendar/0/1/MYR/KUL/KIX/2026-11-01/1/16",
    "https://www.airasia.com/flight/en/gb",
    "https://flights.airasia.com/fp/lfc/v1/lowfare?departStation=KUL&arrivalStation=KIX&beginDate=01%2F11%2F2026&endDate=30%2F11%2F2026&currency=MYR&isDestMultiCity=false&isOriginMultiCity=false",
    "https://www.batikair.com.my/",
]
for u in URLS:
    try:
        r = client().get(u, headers={"Accept-Language": "en"})
        print("URL", r.status_code, r.headers.get("content-type"), u, "::", r.text[:400].replace("\n", " "), flush=True)
    except Exception as e:
        print("URL ERROR", u, type(e).__name__, str(e)[:200], flush=True)

"""临时诊断：AirAsia X（D7）在 Google Flights 用哪种查询方式还能拿到价格。跑完即删。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
from fast_flights import FlightQuery, create_query
from fast_flights.fetcher import URL
from primp import Client
from selectolax.lexbor import LexborHTMLParser

DATES = [("2027-03-25", "2027-04-01"), ("2027-04-05", "2027-04-12"), ("2027-04-15", "2027-04-22")]


def get(q, extra=None, curr="MYR"):
    params = {"tfs": q.to_str(), "hl": "en"}
    if curr:
        params["curr"] = curr
    params.update(extra or {})
    c = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)
    return c.get(URL, params=params).text


def items(html):
    s = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if s is None:
        return None, "no ds:1 block"
    data = s.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return [], "errorHasStatus"
    p = json.loads(data)
    out = []
    for blk in (p[2], p[3]):
        if blk and blk[0]:
            out += blk[0]
    return out, None


def show(label, html):
    its, err = items(html)
    if err:
        print(f"  {label}: {err}")
        return
    rows = []
    for k in its:
        fl = k[0]
        price = k[1][0][1] if k[1] and k[1][0] else None
        rows.append((fl[0], fl[2][0][8] if fl[2] else "?", price))
    d7 = [r for r in rows if r[0] == "D7"]
    print(f"  {label}: {len(rows)} flights, D7={d7}, others={[r for r in rows if r[0] != 'D7'][:4]}")
    for k in its:
        if k[0][0] == "D7":
            print(f"    raw k[1]={json.dumps(k[1])[:300]}")
            print(f"    len(k)={len(k)} tail={json.dumps(k[2:])[:300]}")
            break


def one(d, airlines=("D7",), stops=0):
    return create_query(flights=[FlightQuery(date=d, from_airport="KUL", to_airport="KIX",
                                             airlines=list(airlines) if airlines else None, max_stops=stops)],
                        trip="one-way", currency="MYR", language="en")


def rt(d, r):
    return create_query(flights=[FlightQuery(date=d, from_airport="KUL", to_airport="KIX", airlines=["D7"], max_stops=0),
                                 FlightQuery(date=r, from_airport="KIX", to_airport="KUL", airlines=["D7"], max_stops=0)],
                        trip="round-trip", currency="MYR", language="en")


for d, r in DATES:
    print(f"== {d} (return {r})")
    for label, fn in [
        ("A one-way MYR", lambda: get(one(d))),
        ("B one-way MYR gl=MY", lambda: get(one(d), {"gl": "MY"})),
        ("C one-way no curr", lambda: get(one(d), curr="")),
        ("D one-way USD", lambda: get(one(d), curr="USD")),
        ("E round-trip MYR", lambda: get(rt(d, r))),
        ("F round-trip MYR gl=MY", lambda: get(rt(d, r), {"gl": "MY"})),
        ("G one-way any airline nonstop", lambda: get(one(d, airlines=None))),
    ]:
        try:
            show(label, fn())
        except Exception as e:
            print(f"  {label}: {type(e).__name__}: {e}")
        time.sleep(2)

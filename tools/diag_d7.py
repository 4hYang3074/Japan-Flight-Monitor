"""临时诊断：同一班 AirAsia X 在不同地区（gl）/币种下的 Google Flights 价格。跑完即删。"""
import json
import time

from fast_flights import FlightQuery, create_query
from fast_flights.fetcher import URL
from primp import Client
from selectolax.lexbor import LexborHTMLParser

LEGS = [("KUL", "KIX", "2027-04-07"), ("KIX", "KUL", "2027-04-13"), ("KIX", "KUL", "2027-04-15")]
VARIANTS = [("MYR", None), ("MYR", "MY"), ("MYR", "JP"), ("JPY", "JP"), ("JPY", None), ("JPY", "MY")]


def price(frm, to, d, curr, gl):
    q = create_query(flights=[FlightQuery(date=d, from_airport=frm, to_airport=to, airlines=["D7"], max_stops=0)],
                     trip="one-way", currency=curr, language="en")
    params = {"tfs": q.to_str(), "hl": "en", "curr": curr}
    if gl:
        params["gl"] = gl
    html = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True).get(URL, params=params).text
    s = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if s is None:
        return "no data block"
    data = s.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return "error"
    p = json.loads(data)
    out = []
    for blk in (p[2], p[3]):
        for k in (blk[0] if blk and blk[0] else []):
            fl = k[0]
            out.append(f"{fl[0]} {fl[2][0][8][0]:02d}:{(fl[2][0][8][1:] or [0])[0]:02d} -> {k[1][0][1] if k[1] and k[1][0] else None}")
    return out


for frm, to, d in LEGS:
    print(f"== {frm}->{to} {d}")
    for curr, gl in VARIANTS:
        try:
            print(f"  {curr} gl={gl or '-'}: {price(frm, to, d, curr, gl)}")
        except Exception as e:
            print(f"  {curr} gl={gl or '-'}: {type(e).__name__}: {e}")
        time.sleep(2)

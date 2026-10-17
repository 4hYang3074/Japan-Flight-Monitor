"""临时诊断：Google Flights「订票选项」页（选定航班后）的价格是否比搜索列表更接近 AirAsia 实际价。跑完即删。"""
import base64
import json
import re
import time

from primp import Client
from selectolax.lexbor import LexborHTMLParser

BASE = "https://www.google.com/travel/flights"


def vint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | 0x80 if n else b])
        if not n:
            return out


def f_int(f, v):
    return vint(f << 3) + vint(v)


def f_bytes(f, v):
    v = v.encode() if isinstance(v, str) else v
    return vint(f << 3 | 2) + vint(len(v)) + v


def leg(date, frm, to, code, number=None):
    b = f_bytes(2, date) + f_int(5, 0) + f_bytes(6, code)
    if number:
        b += f_bytes(4, f_bytes(1, frm) + f_bytes(2, date) + f_bytes(3, to) + f_bytes(5, code) + f_bytes(6, number))
    return b + f_bytes(13, f_bytes(2, frm)) + f_bytes(14, f_bytes(2, to))


def tfs(legs, round_trip):
    b = b"".join(f_bytes(3, l) for l in legs) + f_int(8, 1) + f_int(9, 1) + f_int(19, 1 if round_trip else 2)
    return base64.b64encode(b).decode()


def get(path, t):
    c = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)
    return c.get(f"{BASE}/{path}", params={"tfs": t, "hl": "en", "curr": "MYR"}).text


def search_items(html):
    s = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if s is None:
        return []
    data = s.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return []
    p = json.loads(data)
    out = []
    for blk in (p[2], p[3]):
        for k in (blk[0] if blk and blk[0] else []):
            out.append(k)
    return out


def flight_no(k):
    txt = json.dumps(k[0][2][0])
    m = re.search(r'\["D7",\s*"(\d+)"', txt)
    return m.group(1) if m else None


def report(label, html):
    print(f"--- {label}: len={len(html)}")
    soup = LexborHTMLParser(html)
    keys = [s.attributes.get("class") for s in soup.css("script") if (s.attributes.get("class") or "").startswith("ds:")]
    print("    ds blocks:", keys)
    for m in sorted(set(re.findall(r"(?:MYR|RM)\s?\d[\d,]*", html)))[:20]:
        print("    price text:", m)
    for s in soup.css("script"):
        cls = s.attributes.get("class") or ""
        t = s.text()
        if cls.startswith("ds:") and ("AirAsia" in t or "airasia" in t):
            print(f"    [{cls}] {t[:1500]}")
    i = html.find("AirAsia")
    if i >= 0:
        print("    first AirAsia context:", re.sub(r"\s+", " ", html[max(0, i - 300):i + 600]))


OUT = ("2027-04-07", "KUL", "KIX")
RET = ("2027-04-13", "KIX", "KUL")
nums = {}
for d, a, b in (OUT, RET):
    items = search_items(get("search", tfs([leg(d, a, b, "D7")], False)))
    for k in items:
        if k[0][0] == "D7":
            nums[(d, a, b)] = flight_no(k)
            print(f"search {a}->{b} {d}: D7 {nums[(d, a, b)]} price={k[1][0][1] if k[1] and k[1][0] else None}")
    time.sleep(2)

for d, a, b in (OUT, RET):
    n = nums.get((d, a, b))
    if not n:
        print("no flight number for", d, a, b)
        continue
    t = tfs([leg(d, a, b, "D7", n)], False)
    report(f"booking one-way {a}->{b} {d} D7{n}", get("booking", t))
    report(f"search(selected) one-way {a}->{b} {d} D7{n}", get("search", t))
    time.sleep(2)

if len(nums) == 2:
    t = tfs([leg(*[OUT[0], OUT[1], OUT[2]], "D7", nums[OUT]), leg(RET[0], RET[1], RET[2], "D7", nums[RET])], True)
    report("booking round-trip", get("booking", t))
    t1 = tfs([leg(OUT[0], OUT[1], OUT[2], "D7", nums[OUT]), leg(RET[0], RET[1], RET[2], "D7")], True)
    items = search_items(get("search", t1))
    print("round-trip return list after selecting outbound:",
          [(k[0][0], flight_no(k), k[1][0][1] if k[1] and k[1][0] else None) for k in items])

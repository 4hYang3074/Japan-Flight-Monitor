"""临时诊断（第二轮）：Google Flights 在所有国家参数下都不给 AirAsia X 报价；
官网的低价日历接口回 RBAC: access denied（缺请求头）。从官网机票页的 JS 里找出前端实际调用的接口与请求头。"""
import re
from urllib.parse import urljoin

from primp import Client

c = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)
PAGES = ["https://www.airasia.com/flight/en/gb",
         "https://www.airasia.com/flights/search/?origin=KUL&destination=KIX&departDate=10%2F11%2F2026&tripType=O&adult=1&child=0&infant=0&locale=en-gb&currency=MYR&cabinClass=economy"]
KEYS = re.compile(r"lowfare|lfc/|fare-?calendar|pricecalendar|x-api-key|api[_-]?key|channel_hash|user-type|x-aa-|aaw-|bff|/fp/|flights\.airasia\.com|k\.airasia\.com|search/v\d", re.I)
HOST = re.compile(r"https?://[a-z0-9.-]*airasia\.com[A-Za-z0-9_/\-.{}$?=&%]*")

hosts, hits, seen = set(), {}, set()
for page in PAGES:
    r = c.get(page, headers={"Accept-Language": "en"})
    print("PAGE", r.status_code, len(r.text), page, flush=True)
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
    for blob_url, text in [(page, r.text)] + [(urljoin(page, s), None) for s in srcs]:
        if blob_url in seen:
            continue
        seen.add(blob_url)
        if text is None:
            try:
                text = c.get(blob_url).text
            except Exception as e:
                print("JS ERROR", blob_url, e)
                continue
        hosts.update(HOST.findall(text))
        for m in KEYS.finditer(text):
            ctx = text[max(0, m.start() - 160): m.end() + 200].replace("\n", " ")
            k = (m.group(0).lower(), ctx[:80])
            if k not in hits:
                hits[k] = (blob_url.rsplit("/", 1)[-1], ctx)
    print("SCRIPTS", len(srcs), flush=True)

print("=== HOSTS")
for h in sorted(hosts):
    print("HOST", h)
print("=== KEYWORD HITS", len(hits))
for (k, _), (f, ctx) in list(hits.items())[:150]:
    print("HIT", k, "|", f, "|", ctx)

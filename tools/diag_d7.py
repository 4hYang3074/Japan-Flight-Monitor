"""临时诊断：GitHub 机器能否直接从 AirAsia 拿到 KUL→KIX 票价。跑完即删。"""
from primp import Client

URLS = [
    "https://k.airasia.com/availabledates/api/v1/pricecalendar/0/1/MYR/KUL/KIX/2027-03-20/1/31",
    "https://k.airasia.com/availabledates/api/v1/pricecalendar/0/1/MYR/KIX/KUL/2027-03-25/1/31",
    "https://flights.airasia.com/fp/lfc/v1/lowfare?departStation=KUL&arrivalStation=KIX&beginDate=20%2F03%2F2027"
    "&endDate=20%2F04%2F2027&currency=MYR&isDestinationCity=false&isOriginCity=false",
    "https://www.airasia.com/flights/search/?origin=KUL&destination=KIX&departDate=05%2F04%2F2027&tripType=O&adult=1&locale=en-gb&currency=MYR",
    "https://www.airasia.com/en/gb/",
]
for u in URLS:
    for imp in ("chrome_145", None):
        try:
            c = Client(impersonate=imp, impersonate_os="macos", referer=True, cookie_store=True) if imp else Client()
            r = c.get(u, headers={"Accept": "application/json, text/html", "Origin": "https://www.airasia.com",
                                  "Referer": "https://www.airasia.com/"})
            body = r.text
            print(f"== [{imp or 'plain'}] {r.status_code} len={len(body)} {u[:90]}")
            print("   ", body[:600].replace("\n", " "))
        except Exception as e:
            print(f"== [{imp or 'plain'}] {type(e).__name__}: {e} {u[:90]}")

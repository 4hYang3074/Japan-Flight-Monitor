"""每日扫描：逐天抓取各航线两个方向的单程航班（Google Flights），输出 data/raw.json。"""
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from fast_flights import FlightQuery, create_query, get_flights

ROOT = Path(__file__).resolve().parent.parent
SLEEP = 1.5
RETRIES = 3


def scan_window(cfg):
    s = cfg["sakura"]
    if s.get("official"):
        bloom = s["full_bloom"]
        return bloom - timedelta(days=15), bloom + timedelta(days=15)
    w = cfg["default_window"]
    return w["start"], w["end"]


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def fetch(frm, to, d, currency, airlines=None, max_stops=None):
    q = create_query(
        flights=[FlightQuery(date=d.isoformat(), from_airport=frm, to_airport=to,
                             airlines=airlines, max_stops=max_stops)],
        trip="one-way", currency=currency, language="en",
    )
    return run(q)


def run(q):
    """返回 (flights, error)。Google 没有结果时解析器会抛异常，视为空结果。"""
    last = None
    for attempt in range(RETRIES):
        try:
            return list(get_flights(q)), None
        except (IndexError, TypeError, KeyError):
            return [], None
        except Exception as e:  # 网络/被限流
            last = f"{type(e).__name__}: {e}"
            time.sleep(5 * (attempt + 1))
    return [], last


def to_record(f, utc_off, fetched_at, source):
    legs = f.flights
    d, a = legs[0].departure, legs[-1].arrival
    dep = datetime(*d.date, *d.time)
    arr = datetime(*a.date, *a.time)
    o, t = legs[0].from_airport.code, legs[-1].to_airport.code
    minutes = int(((arr - timedelta(hours=utc_off[t])) - (dep - timedelta(hours=utc_off[o]))).total_seconds() // 60)
    return {
        "airline": " / ".join(f.airlines),
        "code": f.type,
        "from": o,
        "to": t,
        "dep": dep.strftime("%Y-%m-%d %H:%M"),
        "arr": arr.strftime("%Y-%m-%d %H:%M"),
        "stops": len(legs) - 1,
        "via": [l.to_airport.code for l in legs[:-1]],
        "minutes": minutes,
        "price": f.price,
        "fetched_at": fetched_at,
        "source": source,
    }


def scan_roundtrips(route, cfg, outbound, inbound, start, end, errors):
    """全服务航司：把回程锁定到单一起飞小时，查出“指定去程+指定回程”的往返票价。"""
    o, k, cur = route["origin"], route["dest"], cfg["currency"]
    results = []
    for code in route.get("roundtrip_airlines", []):
        out_by_date, in_by_date = {}, {}
        for r in outbound:
            if r["code"] == code and r["stops"] == 0:
                out_by_date.setdefault(r["dep"][:10], []).append(r)
        for r in inbound:
            if r["code"] == code and r["stops"] == 0:
                in_by_date.setdefault(r["dep"][:10], []).append(r)
        for d in daterange(start, end):
            outs = out_by_date.get(d.isoformat())
            if not outs:
                continue
            out_times = {r["dep"][11:]: r["dep"] for r in outs}
            for n in cfg["nights"]:
                rd = d + timedelta(days=n)
                rets = in_by_date.get(rd.isoformat(), [])
                hours = [int(r["dep"][11:13]) for r in rets]
                for ret in rets:
                    h = int(ret["dep"][11:13])
                    if hours.count(h) > 1:  # 同一小时有两班，无法唯一锁定
                        continue
                    q = create_query(
                        flights=[FlightQuery(date=d.isoformat(), from_airport=o, to_airport=k,
                                             airlines=[code], max_stops=0),
                                 FlightQuery(date=rd.isoformat(), from_airport=k, to_airport=o,
                                             airlines=[code], max_stops=0,
                                             earliest_departure_hour=h, latest_departure_hour=h)],
                        trip="round-trip", currency=cur, language="en",
                    )
                    flights, err = run(q)
                    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if err:
                        errors.append(f"RT {code} {d}->{rd} {h}h: {err}")
                    for f in flights:
                        if len(f.flights) != 1:
                            continue
                        t = f.flights[0].departure.time
                        dep = out_times.get(f"{t[0]:02}:{t[1]:02}")
                        if dep:
                            results.append({"code": code, "out_dep": dep, "ret_dep": ret["dep"],
                                            "price": f.price, "fetched_at": now})
                    time.sleep(SLEEP)
        print(f"{route['id']} roundtrip {code} done", flush=True)
    return results


def scan_day(frm, to, d, route, cfg, errors, coverage):
    cur = cfg["currency"]
    utc = cfg["airport_utc_offset"]
    found = {}

    def add(rec):
        key = (rec["code"], rec["dep"], rec["arr"], tuple(rec["via"]))
        # 航司筛选查询得到的价格优先于综合查询
        if key not in found or rec["source"] != "general":
            found[key] = rec

    for code, name in route["direct_airlines"].items():
        flights, err = fetch(frm, to, d, cur, airlines=[code], max_stops=0)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if err:
            errors.append(f"{frm}->{to} {d} {code}: {err}")
        recs = [to_record(f, utc, now, f"airline:{code}") for f in flights if len(f.flights) == 1]
        coverage.append({"route": route["id"], "from": frm, "date": d.isoformat(), "code": code,
                         "airline": name, "count": len(recs), "error": bool(err)})
        for r in recs:
            add(r)
        time.sleep(SLEEP)

    flights, err = fetch(frm, to, d, cur)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if err:
        errors.append(f"{frm}->{to} {d} general: {err}")
    conns = []
    for f in flights:
        r = to_record(f, utc, now, "general")
        if r["stops"] == 0:
            add(r)
        elif r["stops"] == 1 and r["minutes"] <= cfg["max_connection_hours"] * 60:
            conns.append(r)
    for r in sorted(conns, key=lambda r: r["price"])[: cfg["max_connections_per_day"]]:
        add(r)
    time.sleep(SLEEP)
    return list(found.values())


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    start, end = scan_window(cfg)
    if os.environ.get("SCAN_LIMIT_DAYS"):
        end = min(end, start + timedelta(days=int(os.environ["SCAN_LIMIT_DAYS"]) - 1))
    ret_end = end + timedelta(days=max(cfg["nights"]))
    ret_start = start + timedelta(days=min(cfg["nights"]))
    out = {"window": [start.isoformat(), end.isoformat()], "routes": {}, "coverage": [], "errors": []}
    t0 = time.time()
    for route in cfg["routes"]:
        o, k = route["origin"], route["dest"]
        outbound, inbound = [], []
        for d in daterange(start, end):
            outbound += scan_day(o, k, d, route, cfg, out["errors"], out["coverage"])
            print(f"{o}->{k} {d} done", flush=True)
        for d in daterange(ret_start, ret_end):
            inbound += scan_day(k, o, d, route, cfg, out["errors"], out["coverage"])
            print(f"{k}->{o} {d} done", flush=True)
        rts = scan_roundtrips(route, cfg, outbound, inbound, start, end, out["errors"])
        out["routes"][route["id"]] = {"outbound": outbound, "inbound": inbound, "roundtrip": rts}
    out["scanned_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["duration_s"] = round(time.time() - t0)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "raw.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done in {out['duration_s']}s, errors={len(out['errors'])}")


if __name__ == "__main__":
    main()

"""把 data/raw.json 组合成往返行程、计算统计，输出 docs/data.json 与 docs/history.json。"""
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MIN_OK_RATIO = 0.3


def load(p, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def sakura_tag(out_d, nights, bloom):
    mid = out_d + timedelta(days=nights / 2)
    diff = (mid - bloom).days
    if abs(diff) <= 3:
        return "核心窗口"
    if abs(diff) <= 6:
        return "接近核心"
    return "较早" if diff < 0 else "较晚"


def baggage_text(codes, bag):
    parts = []
    for c in codes:
        b = bag.get(c)
        kg = b.get("kg") if b else None
        parts.append("待确认" if kg is None else "不含托运" if kg == 0 else f"含{kg}kg")
    return parts[0] if len(set(parts)) == 1 else f"去{parts[0]} / 回{parts[1]}"


def leg_view(r):
    dep = datetime.fromisoformat(r["dep"])
    arr = datetime.fromisoformat(r["arr"])
    plus = (arr.date() - dep.date()).days
    return {
        "al": r["airline"], "c": r["code"],
        "dep": dep.strftime("%H:%M"), "arr": arr.strftime("%H:%M") + (f"+{plus}" if plus else ""),
        "via": r["via"], "min": r["minutes"], "p": r["price"],
    }


def combine(route_raw, cfg, bloom):
    by_date_in = {}
    for r in route_raw["inbound"]:
        by_date_in.setdefault(r["dep"][:10], []).append(r)
    rt = {(x["out_dep"], x["ret_dep"]): x for x in route_raw.get("roundtrip", [])}
    rows = []
    for o in route_raw["outbound"]:
        od = date.fromisoformat(o["dep"][:10])
        for n in cfg["nights"]:
            rd = od + timedelta(days=n)
            for i in by_date_in.get(rd.isoformat(), []):
                price, pt, fetched = o["price"] + i["price"], "单程×2", min(o["fetched_at"], i["fetched_at"])
                x = rt.get((o["dep"], i["dep"])) if o["code"] == i["code"] else None
                if x and x["price"] < price:
                    price, pt, fetched = x["price"], "往返票", x["fetched_at"]
                rows.append({
                    "od": od.isoformat(), "rd": rd.isoformat(), "n": n,
                    "o": leg_view(o), "r": leg_view(i),
                    "direct": o["stops"] == 0 and i["stops"] == 0,
                    "p": price, "pt": pt,
                    "bag": baggage_text([o["code"], i["code"]], cfg["baggage"]),
                    "sk": sakura_tag(od, n, bloom),
                    "f": fetched,
                })
    return rows


def row_key(r):
    return (r["od"], r["n"], r["o"]["c"], r["o"]["dep"], r["r"]["c"], r["r"]["dep"])


def merge_key(r):
    return (r["n"], r["o"]["c"], r["o"]["dep"], r["o"]["arr"], tuple(r["o"]["via"]),
            r["r"]["c"], r["r"]["dep"], r["r"]["arr"], tuple(r["r"]["via"]), r["p"], r["pt"], r["bag"])


def merge_rows(rows):
    """只有航司、时间、价格、行李、晚数完全相同的行才合并；日期列成连续范围或逗号列表。"""
    groups = {}
    for r in rows:
        groups.setdefault(merge_key(r), []).append(r)
    merged = []
    for g in groups.values():
        g.sort(key=lambda r: r["od"])
        base = dict(g[0])
        base["dates"] = [r["od"] for r in g]
        base["sks"] = sorted({r["sk"] for r in g})
        base["chg"] = min((r.get("chg") for r in g if r.get("chg") is not None), default=None)
        merged.append(base)
    return merged


def stats(prices):
    if not prices:
        return None
    return {"n": len(prices), "min": min(prices), "max": max(prices),
            "median": round(statistics.median(prices)), "avg": round(statistics.mean(prices))}


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw = load(ROOT / "data" / "raw.json")
    prev = load(DOCS / "data.json", {})
    prev_routes = {r["id"]: r for r in prev.get("routes", [])}
    bloom = cfg["sakura"]["full_bloom"]
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    routes_out, status_msgs = [], []
    for route in cfg["routes"]:
        rid = route["id"]
        rr = raw["routes"].get(rid, {"outbound": [], "inbound": []})
        n_raw = len(rr["outbound"]) + len(rr["inbound"])
        pr = prev_routes.get(rid)
        if pr and n_raw < pr.get("raw_count", 0) * MIN_OK_RATIO:
            status_msgs.append(f"{route['name']} 今天抓取结果异常偏少（{n_raw} 笔），保留上一次的数据")
            pr["stale"] = True
            routes_out.append(pr)
            continue

        rows = combine(rr, cfg, bloom)
        prev_price = {}
        if pr:
            for r in pr["rows"]:
                for d in r["dates"]:
                    prev_price[(d,) + tuple(row_key(r)[1:])] = r["p"]
        for r in rows:
            old = prev_price.get(row_key(r))
            r["chg"] = r["p"] - old if old is not None else None

        core = [r["p"] for r in rows if r["n"] in cfg["core_nights"]]
        st = stats(core)
        merged = merge_rows(rows)
        for r in merged:
            r["rel"] = round((r["p"] - st["avg"]) / st["avg"] * 100, 1) if st else None
        merged.sort(key=lambda r: (r["p"], r["od"]))

        cov = {}
        for c in raw["coverage"]:
            if c["route"] != rid:
                continue
            e = cov.setdefault(c["code"], {"airline": c["airline"], "days": 0, "found": 0, "errors": 0})
            e["days"] += 1
            e["found"] += c["count"] > 0
            e["errors"] += c["error"]

        core_rows = [r for r in merged if r["n"] in cfg["core_nights"]]
        top = [r for r in core_rows if {"核心窗口", "接近核心"} & set(r["sks"])][:3]
        routes_out.append({
            "id": rid, "name": route["name"], "raw_count": n_raw, "stale": False,
            "stats": st, "prev_stats": pr.get("stats") if pr else None,
            "cheapest": core_rows[0] if core_rows else None,
            "top": top, "coverage": cov, "rows": merged,
        })

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanned_at": raw.get("scanned_at"),
        "window": raw["window"], "currency": cfg["currency"],
        "sakura": {"full_bloom": bloom.isoformat(), "official": cfg["sakura"]["official"],
                   "source": cfg["sakura"]["source"]},
        "core_nights": cfg["core_nights"],
        "errors": len(raw["errors"]), "status": status_msgs,
        "routes": routes_out,
    }
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    hist = load(DOCS / "history.json", [])
    hist = [h for h in hist if h["date"] != today]
    for r in routes_out:
        if r["stats"] and not r["stale"]:
            hist.append({"date": today, "route": r["id"], **r["stats"]})
    (DOCS / "history.json").write_text(json.dumps(hist, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"built: " + ", ".join(f"{r['id']} rows={len(r['rows'])}" for r in routes_out))


if __name__ == "__main__":
    main()

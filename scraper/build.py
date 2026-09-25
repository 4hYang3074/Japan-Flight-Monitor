"""把 data/raw.json 组合成往返行程、计算统计，输出 docs/data.json 与 docs/history.json。"""
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MIN_OK_RATIO = 0.3
MIXED = "混搭（去回不同航司）"


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
                price = pt = fetched = None
                if o["price"] is not None and i["price"] is not None:
                    price, pt, fetched = o["price"] + i["price"], "单程×2", min(o["fetched_at"], i["fetched_at"])
                x = rt.get((o["dep"], i["dep"])) if o["code"] == i["code"] else None
                if x and (price is None or x["price"] < price):
                    price, pt, fetched = x["price"], "往返票", x["fetched_at"]
                if price is None:  # Google 没给价格，不猜
                    continue
                rows.append({
                    "od": od.isoformat(), "rd": rd.isoformat(), "n": n,
                    "o": leg_view(o), "r": leg_view(i),
                    "direct": o["stops"] == 0 and i["stops"] == 0,
                    "ak": o["airline"] if o["airline"] == i["airline"] else MIXED,
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


def airline_stats(rid, rows, hist, today):
    """按航司分别统计今天的价格，并把结果追加到 hist，再结合历史所有扫描算累计值。"""
    groups = {}
    for r in rows:
        groups.setdefault(r["ak"], []).append(r)
    out = []
    for ak, g in groups.items():
        daily = {}
        for r in g:
            daily[r["od"]] = min(daily.get(r["od"], r["p"]), r["p"])
        # 统计对象：每个出发日期当天最便宜的价格
        st = stats(list(daily.values()))
        low = min(g, key=lambda r: (r["p"], r["od"]))
        high_od = max(daily, key=lambda d: (daily[d], d))
        high = min((r for r in g if r["od"] == high_od), key=lambda r: r["p"])
        past = sorted((h for h in hist if h["route"] == rid and h["airline"] == ak), key=lambda h: h["date"])
        prev = past[-1] if past else None
        hist.append({"date": today, "route": rid, "airline": ak, **st, "min_od": low["od"]})
        every = past + [hist[-1]]
        lo = min(every, key=lambda h: (h["min"], h["date"]))
        n_all = sum(h["n"] for h in every)
        past_min = min((h["min"] for h in past), default=None)
        if past_min is None:
            verdict = "首次记录，暂无历史可比"
        elif st["min"] < past_min:
            verdict = f"创历史新低（之前最低 RM{past_min:,}）"
        elif st["min"] <= past_min * 1.03:
            verdict = "接近历史低位，可以考虑入手"
        else:
            verdict = f"比历史最低 RM{past_min:,} 高 {round((st['min'] - past_min) / past_min * 100)}%，可以再观察"
        out.append({
            "past_min": past_min, "verdict": verdict,
            "name": ak, "mixed": ak == MIXED, "direct": any(r["direct"] for r in g), **st,
            "low": {"od": low["od"], "n": low["n"], "dep": low["o"]["dep"]},
            "high": {"od": high["od"], "n": high["n"]},
            "daily": dict(sorted(daily.items())),
            "prev": {"min": prev["min"], "avg": prev["avg"], "date": prev["date"]} if prev else None,
            "all": {"min": lo["min"], "min_od": lo.get("min_od"), "min_seen": lo["date"],
                    "max": max(h["max"] for h in every),
                    "avg": round(sum(h["avg"] * h["n"] for h in every) / n_all),
                    "scans": len(every), "since": every[0]["date"]},
        })
    out.sort(key=lambda a: (a["mixed"], a["min"]))
    return out


SAKURA_PTS = {"核心窗口": 30, "接近核心": 20, "较早": 8, "较晚": 8}


def score(r, airline, route_min):
    """推荐分数 100：价格(对比该航司历史平均)40 + 樱花30 + 全航线比价20 + 便利度10。"""
    pct_below = (airline["all"]["avg"] - r["p"]) / airline["all"]["avg"] * 100
    s_price = round(max(0, min(40, 20 + pct_below)))
    s_sakura = max(SAKURA_PTS[s] for s in r["sks"])
    s_route = round(20 * route_min / r["p"])
    directs = (not r["o"]["via"]) + (not r["r"]["via"])
    s_conv = {2: 10, 1: 5, 0: 0}[directs]
    return [s_price + s_sakura + s_route + s_conv, s_price, s_sakura, s_route, s_conv]


def find_deals(route, rows, by_ak, cfg):
    """划算条件：落在樱花核心/接近核心，且 (创该航司历史新低 或 比历史平均低 ≥ deal_pct% 或 低于自设目标价)。"""
    target = (cfg.get("alert_below") or {}).get(route["id"])
    deals = []
    for r in rows:
        if r["n"] not in cfg["core_nights"] or not {"核心窗口", "接近核心"} & set(r["sks"]):
            continue
        a = by_ak.get(r["ak"])
        reasons = []
        if a and a["all"]["scans"] >= 3 and r["p"] < a["past_min"]:
            reasons.append(f"{a['name']} 历史新低（之前最低 RM{a['past_min']:,}）")
        if a and a["all"]["scans"] > 1:
            pct = (a["all"]["avg"] - r["p"]) / a["all"]["avg"] * 100
            if pct >= cfg["deal_pct"]:
                reasons.append(f"比该航司历史平均低 {round(pct)}%")
        if target and r["p"] <= target:
            reasons.append(f"低于你设的目标价 RM{target:,}")
        if reasons:
            deals.append({**r, "reasons": reasons})
    return deals[:10]


def deal_key(rid, d):
    return (rid, tuple(d["dates"]), d["n"], d["o"]["c"], d["o"]["dep"], d["r"]["c"], d["r"]["dep"], d["p"])


def write_alert(routes_out, prev, cfg):
    """只把上一次还没出现过的划算组合写成 data/alert.md，供工作流开 Issue 通知。"""
    seen = {deal_key(r["id"], d) for r in prev.get("routes", []) for d in r.get("deals", [])}
    lines = []
    for r in routes_out:
        if r["stale"]:
            continue
        for d in r["deals"]:
            if deal_key(r["id"], d) in seen:
                continue
            dates = "、".join(x[5:].replace("-", "/") for x in d["dates"])
            al = d["o"]["al"] if d["o"]["al"] == d["r"]["al"] else f"{d['o']['al']} / 回 {d['r']['al']}"
            lines.append(f"- **RM{d['p']:,}** · {r['name']} · {dates} 出发 {d['n']}晚 · {al}"
                         f"（去 {d['o']['dep']}，回 {d['r']['dep']}）· 推荐分 {d['sc'][0]}\n  - " + "；".join(d["reasons"]))
    alert = ROOT / "data" / "alert.md"
    alert.unlink(missing_ok=True)
    if lines:
        alert.parent.mkdir(exist_ok=True)
        alert.write_text("发现新的划算机票（每人经济舱往返）：\n\n" + "\n".join(lines)
                         + "\n\n打开网页查看完整比价：https://4hyang3074.github.io/Japan-Flight-Monitor/\n\n"
                         "价格是扫描当时 Google Flights 的报价，订票时以航司结账页为准。\n", encoding="utf-8")
        print(f"alert: {len(lines)} new deals")


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
    hist = [h for h in load(DOCS / "history.json", []) if h["date"] != today and "airline" in h]

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

        core_all = [r for r in rows if r["n"] in cfg["core_nights"]]
        st = stats([r["p"] for r in core_all])
        airlines = airline_stats(rid, core_all, hist, today)
        by_ak = {a["name"]: a for a in airlines}
        merged = merge_rows(rows)
        fallback = {"all": {"avg": st["avg"]}} if st else None
        for r in merged:
            a = by_ak.get(r["ak"])
            r["rel"] = round((r["p"] - a["avg"]) / a["avg"] * 100, 1) if a else None
            r["sc"] = score(r, a or fallback, st["min"]) if st else None
        merged.sort(key=lambda r: (r["p"], r["od"]))

        cov = {}
        for c in raw["coverage"]:
            if c["route"] != rid:
                continue
            e = cov.setdefault(c["code"], {"airline": c["airline"], "days": 0, "found": 0, "priced": 0, "errors": 0})
            e["days"] += 1
            e["found"] += c["count"] > 0
            e["priced"] += c.get("priced", c["count"]) > 0
            e["errors"] += c["error"]

        core_rows = [r for r in merged if r["n"] in cfg["core_nights"]]
        top = [r for r in core_rows if {"核心窗口", "接近核心"} & set(r["sks"])][:3]
        best = sorted(core_rows, key=lambda r: (-r["sc"][0], r["p"]))[:5]
        deals = find_deals(route, core_rows, by_ak, cfg)
        routes_out.append({
            "id": rid, "name": route["name"], "raw_count": n_raw, "stale": False,
            "stats": st, "prev_stats": pr.get("stats") if pr else None,
            "airlines": airlines,
            "cheapest": core_rows[0] if core_rows else None,
            "best": best, "deals": deals,
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
    write_alert(routes_out, prev, cfg)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    (DOCS / "history.json").write_text(json.dumps(hist, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"built: " + ", ".join(f"{r['id']} rows={len(r['rows'])}" for r in routes_out))


if __name__ == "__main__":
    main()

"""把 data/raw.json 组合成往返行程、计算统计，输出 docs/data.json 与 docs/history.json。"""
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MIN_OK_RATIO = 0.3
CARRY_DAYS = 7  # Google 只给航班不给价时，沿用同一航班旧价的最长天数
CACHE_DAYS = 30  # docs/leg_prices.json 里的单程价保留天数
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
    """只用直飞航班组合往返；去程与回程可以是不同航司。"""
    by_date_in = {}
    for r in route_raw["inbound"]:
        if r["stops"] == 0:
            by_date_in.setdefault(r["dep"][:10], []).append(r)
    rt = {(x["out_dep"], x["ret_dep"]): x for x in route_raw.get("roundtrip", [])}
    rows = []
    for o in (x for x in route_raw["outbound"] if x["stops"] == 0):
        od = date.fromisoformat(o["dep"][:10])
        for n in cfg["nights"]:
            rd = od + timedelta(days=n)
            for i in by_date_in.get(rd.isoformat(), []):
                price = pt = fetched = None
                old = False
                if o["price"] is not None and i["price"] is not None:
                    price, pt, fetched = o["price"] + i["price"], "单程×2", min(o["fetched_at"], i["fetched_at"])
                    old = bool(o.get("old") or i.get("old"))
                x = rt.get((o["dep"], i["dep"])) if o["code"] == i["code"] else None
                if x and (price is None or x["price"] < price):
                    price, pt, fetched, old = x["price"], "往返票", x["fetched_at"], False
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
                    **({"old": True} if old else {}),
                })
    return rows


def row_key(r):
    return (r["od"], r["n"], r["o"]["c"], r["o"]["dep"], r["r"]["c"], r["r"]["dep"])


def merge_key(r):
    return (r.get("old", False), r["n"], r["o"]["c"], r["o"]["dep"], r["o"]["arr"], tuple(r["o"]["via"]),
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


def airline_stats(rid, rows, hist, today, record=True):
    """按航司分别统计今天的价格，并把结果追加到 hist，再结合历史所有扫描算累计值。
    record=False 用于沿用的旧价：照常显示，但不写进 hist。"""
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
        entry = {"date": today, "route": rid, "airline": ak, **st, "min_od": low["od"]}
        if record:
            hist.append(entry)
        every = past + [entry] if record or not past else past
        lo = min(every, key=lambda h: (h["min"], h["date"]))
        n_all = sum(h["n"] for h in every)
        past_min = min((h["min"] for h in past), default=None)
        if not record:
            verdict = f"Google 今天没给报价，这是 {old_date(g)} 扫描时的旧价"
        elif past_min is None:
            verdict = "首次记录，暂无历史可比"
        elif st["min"] < past_min:
            verdict = f"创历史新低（之前最低 RM{past_min:,}）"
        elif st["min"] <= past_min * 1.03:
            verdict = "接近历史低位，可以考虑入手"
        else:
            verdict = f"比历史最低 RM{past_min:,} 高 {round((st['min'] - past_min) / past_min * 100)}%，可以再观察"
        out.append({
            "past_min": past_min, "verdict": verdict, "old": None if record else old_date(g),
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


def old_date(rows):
    return min(r["f"] for r in rows)[:10]


def leg_key(r):
    return f"{r['from']}|{r['code']}|{r['dep']}"


def fill_old_prices(rr, codes, cache, now):
    """codes 航司今天完全没有报价：用 CARRY_DAYS 天内同一航班最近一次的单程价补上，并标记为旧价。"""
    for r in rr["outbound"] + rr["inbound"]:
        if r["price"] is None and r["code"] in codes:
            hit = cache.get(leg_key(r))
            if hit and now - datetime.fromisoformat(hit[1]) <= timedelta(days=CARRY_DAYS):
                r["price"], r["fetched_at"], r["old"] = hit[0], hit[1], True


def update_cache(rr, cache):
    for r in rr["outbound"] + rr["inbound"]:
        if r["price"] is not None and not r.get("old"):
            cache[leg_key(r)] = [r["price"], r["fetched_at"]]


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
    """值得购买（6/7/8 晚）：
    - 直飞往返每人低于 buy_direct_below（扫描期间任何日期都算）；或
    - 落在樱花核心/接近核心，且 创该航司历史新低 / 比历史平均低 ≥ deal_pct% / 低于自设目标价。"""
    target = (cfg.get("alert_below") or {}).get(route["id"])
    direct_below = cfg.get("buy_direct_below")
    deals = []
    for r in rows:
        if r["n"] not in cfg["core_nights"]:
            continue
        reasons = []
        if direct_below and r["direct"] and r["p"] < direct_below:
            reasons.append(f"直飞往返每人低于 RM{direct_below:,}")
        if not {"核心窗口", "接近核心"} & set(r["sks"]):
            if reasons:
                deals.append({**r, "reasons": reasons})
            continue
        a = by_ak.get(r["ak"])
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
    now = datetime.now(timezone.utc)
    leg_cache = load(DOCS / "leg_prices.json", {})

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

        cov = {}
        for c in raw["coverage"]:
            if c["route"] != rid:
                continue
            e = cov.setdefault(c["code"], {"airline": c["airline"], "days": 0, "found": 0, "priced": 0, "errors": 0})
            e["days"] += 1
            e["found"] += c["count"] > 0
            e["priced"] += c.get("priced", c["count"]) > 0
            e["errors"] += c["error"]

        # 有航班却完全没有报价（例如 AirAsia 大促期间 Google 不显示价格）：用同一航班的旧价补上并标出来
        unpriced = {c for c, e in cov.items() if e["found"] and not e["priced"]}
        fill_old_prices(rr, unpriced, leg_cache, now)
        update_cache(rr, leg_cache)
        rows = combine(rr, cfg, bloom)
        old_rows = [r for r in rows if r.get("old")]
        rows = [r for r in rows if not r.get("old")]
        for r in old_rows:
            r["chg"] = None
        old_legs = [r for r in rr["outbound"] + rr["inbound"] if r.get("old")]
        carried = {r["code"] for r in old_legs}
        old_since = min((r["fetched_at"] for r in old_legs), default="")[:10]
        for codes, tail in ((carried, f"先显示最近一次查到的旧价（{old_since} 起，标“旧价”，不计入今日最低与提醒）；实时价请点航班旁的官网按钮"),
                            (unpriced - carried, "暂时无法比价，请到航司官网查看")):
            if codes:
                names = "、".join(cov[c]["airline"] for c in sorted(codes))
                status_msgs.append(f"{route['name']}：{names} 今天在 Google Flights 有航班但没有报价，{tail}")
        tp_codes = {r["code"] for r in rr["outbound"] + rr["inbound"] if r.get("source") == "travelpayouts"}
        if tp_codes:
            names = "、".join(cov.get(c, {}).get("airline", c) for c in sorted(tp_codes))
            status_msgs.append(f"{route['name']}：{names} 在 Google Flights 没有报价的航班，改用 Aviasales 最近 2–7 天的缓存价（非实时，下单前请到官网确认）")
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
        fresh_ak = {a["name"] for a in airlines}
        old_core = [r for r in old_rows if r["n"] in cfg["core_nights"] and r["ak"] not in fresh_ak]
        airlines += airline_stats(rid, old_core, hist, today, record=False)
        airlines.sort(key=lambda a: (a["mixed"], a["min"]))
        by_ak = {a["name"]: a for a in airlines}
        merged = merge_rows(rows + old_rows)
        score_st = st or stats([r["p"] for r in old_core])
        fallback = {"all": {"avg": score_st["avg"]}} if score_st else None
        for r in merged:
            a = by_ak.get(r["ak"])
            r["rel"] = round((r["p"] - a["avg"]) / a["avg"] * 100, 1) if a else None
            r["sc"] = score(r, a or fallback, score_st["min"]) if score_st else None
        merged.sort(key=lambda r: (r["p"], r["od"]))

        core_rows = [r for r in merged if r["n"] in cfg["core_nights"]]
        fresh_core = [r for r in core_rows if not r.get("old")]
        top = [r for r in fresh_core if {"核心窗口", "接近核心"} & set(r["sks"])][:3]
        best = sorted(core_rows, key=lambda r: (-r["sc"][0], r["p"]))[:5]
        deals = find_deals(route, fresh_core, by_ak, cfg)
        routes_out.append({
            "id": rid, "name": route["name"], "raw_count": n_raw, "stale": False,
            "stats": st, "prev_stats": pr.get("stats") if pr else None,
            "airlines": airlines,
            "cheapest": fresh_core[0] if fresh_core else None,
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
    leg_cache = {k: v for k, v in sorted(leg_cache.items())
                 if now - datetime.fromisoformat(v[1]) <= timedelta(days=CACHE_DAYS)}
    (DOCS / "leg_prices.json").write_text(json.dumps(leg_cache, separators=(",", ":")), encoding="utf-8")
    print(f"built: " + ", ".join(f"{r['id']} rows={len(r['rows'])}" for r in routes_out))


if __name__ == "__main__":
    main()

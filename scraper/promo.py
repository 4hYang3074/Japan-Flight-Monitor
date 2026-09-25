"""AirAsia 促销监控：读取官网促销页与官方新闻室 RSS，发现与本行程相关的新促销时输出 data/promo_new.json。
用法：python scraper/promo.py          检查促销，写 docs/promos.json
      python scraper/promo.py report   有新促销时，结合加扫结果写 data/promo_alert.md"""
import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from selectolax.lexbor import LexborHTMLParser

from build import DOCS, ROOT, combine, load, row_key

import yaml

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
PROMO_PAGE = "https://www.airasia.com/en/gb/promotions"
RSS_FEEDS = ["https://newsroom.airasia.com/news?format=rss", "https://newsroom.airasia.com/stories?format=rss"]
SALE_WORDS = re.compile(r"\bsale\b|% ?off|\boff\b|free seat|free seats|promo|low fares?|deal|discount|mega|big sale|\bfrom (myr|rm)", re.I)
FLIGHT = re.compile(r"flight|fly\b|fares?\b|seats?\b|airasia x|/flights?/", re.I)
SCOPE = re.compile(r"all seats|all flights|all routes|airasia x|\bd7\b|japan|osaka|kansai|\bkix\b", re.I)
BIG_SALE_BANNER = re.compile(r"mega ?sale|big ?sale|free ?seat|all ?seats", re.I)
EXCLUDE = re.compile(r"\brides?\b|duty.?free|movetix|\bevent\b|insurance|philippines|thai airasia|indonesia|cambodia|\bphp\b|\bidr\b|\bthb\b", re.I)
NEW_FILE = ROOT / "data" / "promo_new.json"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def pid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def from_promo_page():
    data = json.loads(LexborHTMLParser(get(PROMO_PAGE)).css_first("script#__NEXT_DATA__").text())
    found = {}

    def walk(o):
        if isinstance(o, dict):
            title = o.get("title") or o.get("headline") or ""
            sub = o.get("subtitle") or o.get("subTitle") or o.get("subheadline") or ""
            ban = o.get("bannerID") or ""
            text = f"{title} {sub} {ban}"
            if isinstance(title, str) and isinstance(sub, str) and SALE_WORDS.search(text):
                url = o.get("url") or o.get("redirectUrl") or PROMO_PAGE
                label = title or f"官网横幅（代码 {ban}）"
                if not title and BIG_SALE_BANNER.search(ban):
                    label = f"大促预告横幅（代码 {ban}）"
                found[pid("page", label, sub)] = {"source": "AirAsia 官网促销页", "title": label.strip(),
                                                  "detail": sub.strip(), "url": url}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found


def from_rss(url, days=30):
    root = ET.fromstring(get(url))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    found = {}
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
        except (TypeError, ValueError):
            continue
        if when < cutoff or not SALE_WORDS.search(title) or not title.isascii():
            continue
        found[pid("rss", link)] = {"source": "AirAsia 官方新闻室", "title": title,
                                   "detail": re.sub(r"\s+", " ", desc).strip()[:200], "url": link,
                                   "published": when.date().isoformat()}
    return found


def relevant(p):
    """机票促销，且是全航线类大促、或与 AirAsia X / 日本 / 大阪有关；大促预告横幅也算。"""
    text = f"{p['title']} {p['detail']} {p['url']}"
    if EXCLUDE.search(text):
        return False
    return bool(BIG_SALE_BANNER.search(p["title"]) or (FLIGHT.search(text) and SCOPE.search(text)))


def check():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state_file = DOCS / "promos.json"
    first_run = not state_file.exists()
    state = load(state_file, {"seen": {}, "new_history": []})
    current, errors = {}, []
    for name, fn in [("官网促销页", from_promo_page)] + [(u, lambda u=u: from_rss(u)) for u in RSS_FEEDS]:
        try:
            current.update(fn())
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
    for k, p in current.items():
        p["id"], p["relevant"] = k, relevant(p)
    new = [p for k, p in current.items() if k not in state["seen"] and p["relevant"]]
    for k in current:
        state["seen"].setdefault(k, now)
    if new and not first_run:
        for p in new:
            p["found_at"] = now
        state["new_history"] = (new + state["new_history"])[:50]
        NEW_FILE.parent.mkdir(exist_ok=True)
        NEW_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    before = {k: v for k, v in state.items() if k != "updated_at"}
    state.update({"errors": errors,
                  "current": sorted(current.values(), key=lambda p: (not p["relevant"], p["title"]))})
    # 只在促销内容有变化时才写文件，避免每次检查都产生无意义的提交
    if first_run or {k: v for k, v in state.items() if k != "updated_at"} != before:
        state["updated_at"] = now
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    has_new = bool(new) and not first_run
    print(f"promos: {len(current)} current, {len(new)} new relevant, first_run={first_run}, errors={errors}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"new={'true' if has_new else 'false'}\n")


def report():
    """写 Issue 内容：新促销 + 刚加扫的 AirAsia X 樱花期最便宜组合（与今早例行扫描比较）。"""
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    new = load(NEW_FILE, [])
    raw = load(ROOT / "data" / "raw_watch.json")
    daily = load(DOCS / "data.json", {})
    lines = ["AirAsia 发布了新促销：", ""]
    for p in new:
        lines.append(f"- **{p['title']}**" + (f" — {p['detail']}" if p["detail"] else "") + f"\n  - 来源：{p['source']} · {p['url']}")
    lines += ["", "已立即加扫 AirAsia X（KUL↔KIX 直飞），樱花期最便宜的组合（每人往返，6/7/8 晚）：", ""]
    base = {}
    for r in next((x for x in daily.get("routes", []) if x["id"] == "KUL-KIX"), {}).get("rows", []):
        for d in r["dates"]:
            base[(d,) + row_key(r)[1:]] = r["p"]
    rows = []
    for rr in (raw or {}).get("routes", {}).values():
        rows += [r for r in combine(rr, cfg, cfg["sakura"]["full_bloom"])
                 if r["n"] in cfg["core_nights"] and r["sk"] in ("核心窗口", "接近核心")]
    for r in sorted(rows, key=lambda r: r["p"])[:8]:
        old = base.get(row_key(r))
        cmp = "" if old is None else ("（与今早相同）" if old == r["p"] else f"（今早 RM{old:,}，{'降' if r['p'] < old else '涨'} RM{abs(r['p'] - old):,}）")
        lines.append(f"- **RM{r['p']:,}** · {r['od'][5:].replace('-', '/')} 出发 {r['n']}晚 · 去 {r['o']['dep']} 回 {r['r']['dep']} {cmp}")
    if not rows:
        lines.append("- 这次加扫没有取得樱花期价格（可能促销尚未反映到 Google Flights，建议直接打开 AirAsia MOVE App 查看）。")
    lines += ["", "注意：会员/App 专属价与优惠码折扣不会显示在 Google Flights，促销期间请同时打开 AirAsia MOVE App 查看。",
              "", "网页：https://4hyang3074.github.io/Japan-Flight-Monitor/"]
    (ROOT / "data" / "promo_alert.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    report() if sys.argv[1:] == ["report"] else check()

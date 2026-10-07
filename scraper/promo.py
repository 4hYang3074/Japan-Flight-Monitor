"""AirAsia 促销监控：读取官网促销页与官方新闻室 RSS，发现与本行程相关的新促销时输出 data/promo_new.json。
用法：python scraper/promo.py            检查促销，写 docs/promos.json（只用标准库，本机定时任务无需安装套件）
      python scraper/promo.py from-push  GitHub 收到本机推送后，找出本机发现的新促销
      python scraper/promo.py report     有新促销时，结合加扫结果写 data/promo_alert.md"""
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
NEXT_F = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)


def load(p, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
# 原促销页 /en/gb/promotions 自 2026-10 起转址到首页，促销改为首页的大横幅与“Promotions”卡片
PROMO_PAGE = "https://www.airasia.com/en/gb/"
RSS_FEEDS = ["https://newsroom.airasia.com/news?format=rss", "https://newsroom.airasia.com/stories?format=rss"]
SALE_WORDS = re.compile(r"\bsale\b|% ?off|\boff\b|free seat|free seats|promo|low fares?|deal|discount|mega|big sale|\bfrom (myr|rm)", re.I)
FLIGHT = re.compile(r"flight|fly\b|fares?\b|seats?\b|airasia x|/flights?/", re.I)
SCOPE = re.compile(r"all seats|all flights|all routes|airasia x|\bd7\b|japan|osaka|kansai|\bkix\b", re.I)
BIG_SALE_BANNER = re.compile(r"mega ?sale|big ?sale|free ?seat|all ?seats", re.I)
EXCLUDE = re.compile(r"\brides?\b|duty.?free|movetix|\bevent\b|insurance|philippines|thai airasia|indonesia|cambodia|\bphp\b|\bidr\b|\bthb\b", re.I)
NEW_FILE = ROOT / "data" / "promo_new.json"
DEADLINE = re.compile(r"(?:book(?:ing)?\s+by|until|till|ends?(?:\s+on)?|before|expir\w*(?:\s+on)?)\s+"
                      r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", re.I)
BANNER_DATE = re.compile(r"_(\d{2})(\d{2})(\d{2})_")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
STALE_BANNER_DAYS = 45


def deadline(text):
    """找出“Book by 27 Sep 2026 / till 1 March 2026”这类订票截止日期，没有写就返回 None。"""
    m = DEADLINE.search(text)
    if not m or m.group(2)[:3].lower() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    except ValueError:
        return None


def banner_date(code):
    """官网横幅代码里的日期（如 MOVE_MEGASALE-Teaser_141024 → 2024-10-14）。"""
    m = BANNER_DATE.search(f"{code}_")
    if not m:
        return None
    try:
        return date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def ssl_context():
    """系统证书库之外再加 certifi（若已安装）。Windows 证书库可能缺 ISRG Root X2，
    验证 Let's Encrypt 新证书链时会走到 2025-09 已过期的交叉签名而报 certificate has expired。"""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


SSL_CTX = ssl_context()


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


def pid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


class WrongMarket(Exception):
    pass


def page_data(html):
    """官网页面数据：旧版 Next.js 放在 __NEXT_DATA__；新版（App Router）分散在多段 self.__next_f.push(...) 里，
    拼起来后每行是“编号:JSON”。"""
    m = NEXT_DATA.search(html)
    if m:
        return json.loads(m.group(1))
    stream = "".join(c[1] for c in map(json.loads, NEXT_F.findall(html)) if len(c) > 1 and isinstance(c[1], str))
    data = []
    for line in stream.split("\n"):
        key, _, body = line.partition(":")
        if re.fullmatch(r"[0-9a-f]+", key):
            try:
                data.append(json.loads(body))
            except ValueError:
                pass
    if not data:
        # 页面结构又变了，或被防爬虫挡下；记下页面标题方便判断是哪一种
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        raise ValueError(f"页面里找不到页面数据（{len(html)} 字节，标题：{title.group(1).strip()[:80] if title else '无'}）")
    return data


def find_key(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def from_promo_page():
    data = page_data(get(PROMO_PAGE))
    # AirAsia 按访问者 IP 决定国家版本；非马来西亚版本里没有马来西亚的促销
    geo = find_key(data, "geoId")
    if geo != "MY":
        raise WrongMarket(f"官网促销页按 IP 显示了 {geo} 版本，读不到马来西亚促销，沿用上次马来西亚版结果")
    found = {}

    def walk(o):
        if isinstance(o, dict):
            title = o.get("title") or o.get("headline") or ""
            sub = o.get("subtitle") or o.get("subTitle") or o.get("subheadline") or ""
            # 只看带图片的横幅/卡片，避开导航栏、产品图标里的“Promotions”“Deals”字样
            img = o.get("backgroundImageUrl") or (o.get("backgroundImage") or {}).get("src") or ""
            ban = o.get("bannerID") or (o.get("label") if img else "") or ""
            text = f"{title} {sub} {ban} {img.rsplit('/', 1)[-1]}"
            if (img or ban) and isinstance(title, str) and isinstance(sub, str) and isinstance(ban, str) and SALE_WORDS.search(text):
                url = o.get("url") or o.get("redirectUrl") or o.get("redirectionUrl") or PROMO_PAGE
                label = title or f"官网横幅（代码 {ban}）"
                if not title and BIG_SALE_BANNER.search(ban):
                    label = f"官网大促横幅（代码 {ban}）"
                found[pid("page", label, sub)] = {"source": "AirAsia 官网促销页", "title": label.strip(),
                                                  "detail": sub.strip(), "url": url, "banner": ban,
                                                  "deadline_text": text}
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
                                   "published": when.date().isoformat(), "deadline_text": f"{title} {desc}"}
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
    notes = []

    def keep_page_results():
        for p in state.get("current", []):
            if p["source"] == "AirAsia 官网促销页":
                current[p["id"]] = {k: v for k, v in p.items() if k not in ("id", "relevant", "expired", "deadline")} | {
                    "deadline_text": f"book by {date.fromisoformat(p['deadline']):%d %b %Y}" if p.get("deadline") else ""}

    sources = [(u, lambda u=u: from_rss(u)) for u in RSS_FEEDS]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub 不在马来西亚，读不到马来西亚促销：官网促销页只由本机检查，这里沿用本机上次的结果与报错
        keep_page_results()
        errors += [e for e in state.get("errors", []) if e.startswith("官网促销页")]
    else:
        sources.insert(0, ("官网促销页", from_promo_page))
    for name, fn in sources:
        try:
            current.update(fn())
        except WrongMarket as e:
            notes.append(str(e))
            keep_page_results()
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
    today = datetime.now(timezone(timedelta(hours=8))).date()
    for k, p in current.items():
        dl = deadline(p.pop("deadline_text", ""))
        bd = banner_date(p.pop("banner", "") or p["title"])
        p["deadline"] = dl.isoformat() if dl else None
        # 已过订票截止日，或横幅代码显示是很久以前的活动，都不算有用信息
        p["expired"] = bool((dl and dl < today) or (bd and (today - bd).days > STALE_BANNER_DAYS))
        p["id"], p["relevant"] = k, relevant(p) and not p["expired"]
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
    state.pop("notes", None)
    state.update({"errors": errors,
                  "current": sorted(current.values(), key=lambda p: (not p["relevant"], p["title"]))})
    # 只在促销内容有变化时才写文件，避免每次检查都产生无意义的提交
    if first_run or {k: v for k, v in state.items() if k != "updated_at"} != before:
        state["updated_at"] = now
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    has_new = bool(new) and not first_run
    print(f"promos: {len(current)} current, {len(new)} new relevant, first_run={first_run}, errors={errors}, notes={notes}")
    set_output(has_new)


def set_output(has_new):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"new={'true' if has_new else 'false'}\n")


def from_push():
    """本机（马来西亚 IP）检查后推送 promos.json；比较推送前后的 new_history，找出本机发现的新促销。"""
    try:
        before = json.loads(subprocess.run(["git", "show", "HEAD~1:docs/promos.json"], cwd=ROOT,
                                           capture_output=True, check=True).stdout.decode("utf-8"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        before = {}
    seen = {p["id"] for p in before.get("new_history", [])}
    new = [p for p in load(DOCS / "promos.json", {}).get("new_history", []) if p["id"] not in seen]
    if new:
        NEW_FILE.parent.mkdir(exist_ok=True)
        NEW_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"from push: {len(new)} new promos")
    set_output(bool(new))


def report():
    """写 Issue 内容：新促销 + 刚加扫的 AirAsia X 樱花期最便宜组合（与最近一次例行扫描比较）。"""
    import yaml
    from build import combine, row_key
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
        cmp = "" if old is None else ("（与上次例行扫描相同）" if old == r["p"] else f"（上次例行扫描 RM{old:,}，{'降' if r['p'] < old else '涨'} RM{abs(r['p'] - old):,}）")
        lines.append(f"- **RM{r['p']:,}** · {r['od'][5:].replace('-', '/')} 出发 {r['n']}晚 · 去 {r['o']['dep']} 回 {r['r']['dep']} {cmp}")
    if not rows:
        lines.append("- 这次加扫没有取得樱花期价格（可能促销尚未反映到 Google Flights，建议直接打开 AirAsia MOVE App 查看）。")
    lines += ["", "注意：会员/App 专属价与优惠码折扣不会显示在 Google Flights，促销期间请同时打开 AirAsia MOVE App 查看。",
              "", "网页：https://4hyang3074.github.io/Japan-Flight-Monitor/"]
    (ROOT / "data" / "promo_alert.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    {"report": report, "from-push": from_push}.get(sys.argv[1] if len(sys.argv) > 1 else "", check)()

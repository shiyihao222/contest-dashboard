#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contest_tracker.py — 聚合 Codeforces / AtCoder / 牛客 / 洛谷 比赛信息
GitHub Actions 专用版本：生成静态 HTML，倒计时由前端 JS 计算
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("请先安装 requests:  pip install requests")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
}

TZ_SHANGHAI = timezone(timedelta(hours=8))


def fmt_dur(seconds):
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if m else f"{h}h"


# ---------------- Codeforces ----------------
def fetch_codeforces(session):
    url = "https://codeforces.com/api/contest.list"
    data = session.get(url, headers=HEADERS, timeout=15).json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Codeforces API 异常: {data.get('comment')}")
    return [{
        "platform": "CF",
        "name": c["name"],
        "start": c["startTimeSeconds"],
        "duration": c["durationSeconds"],
        "url": f"https://codeforces.com/contest/{c['id']}",
        "rated": c["phase"] not in ("BEFORE",),
    } for c in data["result"]]


# ---------------- AtCoder ----------------
def fetch_atcoder(session):
    url = "https://kenkoooo.com/atcoder/resources/contests.json"
    data = session.get(url, headers=HEADERS, timeout=20).json()
    return [{
        "platform": "ATC",
        "name": c["title"],
        "start": c["start_epoch_second"],
        "duration": c["duration_second"],
        "url": f"https://atcoder.jp/contests/{c['id']}",
        "rated": c.get("rate_change") not in ("-", None, ""),
    } for c in data]


# ---------------- 牛客 ----------------
def _deep_find_contests(node, found):
    if isinstance(node, dict):
        if "contestName" in node and ("startTime" in node or "beginTime" in node):
            found.append(node)
        for v in node.values():
            _deep_find_contests(v, found)
    elif isinstance(node, list):
        for v in node:
            _deep_find_contests(v, found)


def fetch_nowcoder(session):
    url = "https://ac.nowcoder.com/acm/contest/vip-index"
    html = session.get(url, headers={**HEADERS, "Referer": "https://ac.nowcoder.com/"},
                       timeout=15).text
    m = re.search(r"window\.pageData\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("牛客页面结构变化")
    page = json.loads(m.group(1))
    found = []
    _deep_find_contests(page, found)
    out, seen = [], set()
    for c in found:
        name, cid = c.get("contestName"), c.get("contestId")
        if not name or cid in seen:
            continue
        seen.add(cid)
        st = c.get("startTime") or c.get("beginTime")
        et = c.get("endTime")
        start = int(time.mktime(time.strptime(st, "%Y-%m-%d %H:%M"))) - time.timezone \
            if isinstance(st, str) and re.match(r"\d{4}-\d{2}-\d{2}", st) else None
        duration = None
        if isinstance(et, str) and start:
            end = int(time.mktime(time.strptime(et, "%Y-%m-%d %H:%M"))) - time.timezone
            duration = end - start
        out.append({
            "platform": "NC", "name": name, "start": start,
            "duration": duration,
            "url": f"https://ac.nowcoder.com/acm/contest/{cid}",
            "rated": "Rated" in json.dumps(c, ensure_ascii=False),
        })
    return [o for o in out if o["start"]]


# ---------------- 洛谷 ----------------
def fetch_luogu(session):
    url = "https://www.luogu.com.cn/contest/list?page=1&_contentOnly=1"
    data = session.get(url, headers={**HEADERS, "Referer": "https://www.luogu.com.cn/"},
                       timeout=15).json()
    return [{
        "platform": "LG",
        "name": c["name"],
        "start": c["startTime"],
        "duration": c["endTime"] - c["startTime"],
        "url": f"https://www.luogu.com.cn/contest/{c['id']}",
        "rated": None,
    } for c in data["currentData"]["contests"]["result"]]


FETCHERS = {"cf": fetch_codeforces, "atc": fetch_atcoder,
            "nc": fetch_nowcoder, "lg": fetch_luogu}


def collect(platforms):
    session = requests.Session()
    all_contests, errors = [], {}
    for p in platforms:
        try:
            all_contests.extend(FETCHERS[p](session))
        except Exception as e:
            errors[p] = str(e)
    return all_contests, errors


# ---------------- HTML 生成 ----------------
PLATFORM_META = {
    "CF":  {"name": "Codeforces", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)"},
    "ATC": {"name": "AtCoder",    "color": "#f59e0b", "bg": "rgba(245,158,11,.15)"},
    "NC":  {"name": "牛客",       "color": "#22c55e", "bg": "rgba(34,197,94,.15)"},
    "LG":  {"name": "洛谷",       "color": "#a855f7", "bg": "rgba(168,85,247,.15)"},
}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_html(upcoming, recent, tz, gen_time):
    """生成纯静态 HTML，倒计时由前端 JS 计算"""
    # 按日期分组 upcoming
    up_by_day = defaultdict(list)
    for c in sorted(upcoming, key=lambda x: x["start"]):
        up_by_day[datetime.fromtimestamp(c["start"], tz).date()].append(c)
    
    cards = []
    for day in sorted(up_by_day):
        wd = "一二三四五六日"[day.weekday()]
        cards.append(f'<div class="day-title">📅 {day} 周{wd} ({len(up_by_day[day])} 场)</div>')
        cards.append('<div class="grid">')
        for c in up_by_day[day]:
            meta = PLATFORM_META.get(c["platform"], PLATFORM_META["CF"])
            start_str = datetime.fromtimestamp(c["start"], tz).strftime("%Y-%m-%d %H:%M")
            rated = " · Rated" if c.get("rated") else ""
            cards.append(f"""
      <div class="card" data-platform="{c['platform']}" data-start="{c['start']}">
        <div class="badge" style="color:{meta['color']};background:{meta['bg']}">{meta['name']}</div>
        <div class="card-name"><a href="{_esc(c['url'])}" target="_blank">{_esc(c['name'])}</a></div>
        <div class="card-meta">🕒 {start_str} &nbsp;·&nbsp; ⏱ {fmt_dur(c['duration'] or 0)}{rated}</div>
        <div class="countdown" data-start="{c['start']}"></div>
      </div>""")
        cards.append('</div>')
    upcoming_html = "\n".join(cards) if cards else '<p class="empty">近期没有检测到 upcoming 比赛</p>'

    # recent 表格
    recent_rows = "".join(
        f"<tr data-platform='{c['platform']}'>"
        f"<td><span class='dot' style='background:{PLATFORM_META[c['platform']]['color']}'></span>"
        f"{PLATFORM_META[c['platform']]['name']}</td>"
        f"<td>{datetime.fromtimestamp(c['start'], tz).strftime('%m-%d %H:%M')}</td>"
        f"<td>{_esc(c['name'])}</td>"
        f"<td><a href='{_esc(c['url'])}' target='_blank'>链接</a></td></tr>"
        for c in sorted(recent, key=lambda x: -x["start"]))
    recent_html = recent_rows or '<tr><td colspan="4" class="empty">无</td></tr>'

    # 生成完整 HTML（含前端倒计时 JS）
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>比赛信息看板</title>
<style>
  :root {{ --bg:#0f172a; --panel:#1e293b; --text:#e2e8f0; --muted:#94a3b8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
  header {{ padding:24px 20px 8px; max-width:960px; margin:auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .filters {{ max-width:960px; margin:12px auto; padding:0 20px; display:flex; gap:8px; flex-wrap:wrap; }}
  .chip {{ border:1px solid #334155; background:var(--panel); color:var(--text);
          padding:6px 14px; border-radius:999px; cursor:pointer; font-size:13px; }}
  .chip.active {{ background:#38bdf8; color:#0f172a; border-color:#38bdf8; font-weight:600; }}
  main {{ max-width:960px; margin:auto; padding:0 20px 60px; }}
  .day-title {{ margin:22px 0 10px; font-weight:600; color:#cbd5e1; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; }}
  .card {{ background:var(--panel); border:1px solid #334155; border-radius:12px; padding:14px 16px; }}
  .badge {{ display:inline-block; font-size:12px; padding:2px 10px; border-radius:999px; margin-bottom:8px; }}
  .card-name {{ font-size:15px; font-weight:600; margin-bottom:6px; }}
  .card-name a {{ color:var(--text); text-decoration:none; }}
  .card-name a:hover {{ color:#38bdf8; text-decoration:underline; }}
  .card-meta {{ font-size:12px; color:var(--muted); }}
  .countdown {{ margin-top:8px; font-size:13px; color:#38bdf8; font-variant-numeric:tabular-nums; }}
  .countdown.live {{ color:#f87171; font-weight:600; }}
  h2 {{ font-size:17px; margin:34px 0 10px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border-radius:12px; overflow:hidden; font-size:13px; }}
  th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #334155; }}
  th {{ color:var(--muted); font-weight:500; }}
  a {{ color:#38bdf8; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }}
  .empty {{ color:var(--muted); padding:16px; }}
  tr:hover td {{ background:#263548; }}
  footer {{ max-width:960px; margin:auto; padding:0 20px 30px; color:var(--muted); font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>🏁 比赛信息看板</h1>
  <div class="sub">Codeforces · AtCoder · 牛客 · 洛谷 ｜ 数据更新: {gen_time}</div>
</header>
<div class="filters" id="filters">
  <button class="chip active" data-p="ALL">全部</button>
  <button class="chip" data-p="CF">Codeforces</button>
  <button class="chip" data-p="ATC">AtCoder</button>
  <button class="chip" data-p="NC">牛客</button>
  <button class="chip" data-p="LG">洛谷</button>
</div>
<main>
  <h2>⏳ 即将开始 (upcoming)</h2>
  <div id="upcoming">{upcoming_html}</div>
  <h2>📝 最近结束 (recent)</h2>
  <table>
    <thead><tr><th>平台</th><th>时间</th><th>比赛</th><th></th></tr></thead>
    <tbody id="recent">{recent_html}</tbody>
  </table>
</main>
<footer>由 GitHub Actions 自动更新 · 每 6 小时刷新一次数据</footer>
<script>
// 平台筛选
document.getElementById('filters').addEventListener('click', e => {{
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('.chip').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  applyFilter(e.target.dataset.p);
}});
function applyFilter(p) {{
  document.querySelectorAll('#upcoming .card, #recent tr[data-platform]').forEach(el => {{
    el.style.display = (p === 'ALL' || el.dataset.platform === p) ? '' : 'none';
  }});
  document.querySelectorAll('#upcoming .day-title').forEach(t => {{
    let el = t.nextElementSibling, visible = false;
    while (el && !el.classList.contains('day-title')) {{
      if (el.classList.contains('grid'))
        [...el.children].forEach(c => {{ if (c.style.display !== 'none') visible = true; }});
      el = el.nextElementSibling;
    }}
    t.style.display = visible ? '' : 'none';
  }});
}}
// 前端倒计时（根据当前时间实时计算）
function tick() {{
  const nowMs = Date.now();
  document.querySelectorAll('.countdown').forEach(el => {{
    const diff = el.dataset.start * 1000 - nowMs;
    if (diff <= 0) {{ el.textContent = '🔴 正在进行或已开始'; el.classList.add('live'); return; }}
    const d = Math.floor(diff / 86400000);
    const h = Math.floor(diff % 86400000 / 3600000);
    const m = Math.floor(diff % 3600000 / 60000);
    const s = Math.floor(diff % 60000 / 1000);
    el.textContent = '⏳ 倒计时 ' + (d > 0 ? d + '天 ' : '') +
      String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
  }});
}}
tick(); setInterval(tick, 1000);
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-u", "--upcoming", type=int, default=30, help="upcoming 天数")
    ap.add_argument("-r", "--recent", type=int, default=7, help="recent 天数")
    ap.add_argument("-p", "--platforms", nargs="+",
                    choices=list(FETCHERS), default=list(FETCHERS))
    ap.add_argument("--html", metavar="FILE", required=True, help="输出 HTML 文件")
    args = ap.parse_args()

    tz = TZ_SHANGHAI
    now = time.time()

    contests, errors = collect(args.platforms)
    for p, err in errors.items():
        print(f"[警告] {p} 抓取失败: {err}", file=sys.stderr)

    upcoming = [c for c in contests if c["start"] and now - 1800 <= c["start"]
                <= now + args.upcoming * 86400]
    recent = [c for c in contests if c["start"]
              and now - args.recent * 86400 <= c["start"] < now]

    gen = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    with open(args.html, "w", encoding="utf-8") as fp:
        fp.write(render_html(upcoming, recent, tz, gen))
    print(f"HTML 已生成: {args.html}")


if __name__ == "__main__":
    main()

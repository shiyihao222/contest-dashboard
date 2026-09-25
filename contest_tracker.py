#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contest_tracker.py — 聚合 Codeforces / AtCoder / 牛客 / 洛谷 的比赛信息，辅助安排练习计划

依赖: requests   (pip install requests)

用法:
    python contest_tracker.py                     # 终端显示 upcoming(14天) + recent(7天)
    python contest_tracker.py -u 30 -r 7          # 自定义时间范围(天)
    python contest_tracker.py -p cf atc           # 只看指定平台: cf / atc / nc / lg
    python contest_tracker.py --json out.json     # 导出 JSON
    python contest_tracker.py --ics plan.ics      # 导出日历文件
    python contest_tracker.py --html d.html       # 生成静态 HTML 仪表盘
    python contest_tracker.py --serve             # 启动网页服务, 页内按钮一键刷新
    python contest_tracker.py --serve --port 8080 # 换端口
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


def fmt_time(epoch, tz):
    return datetime.fromtimestamp(epoch, tz).strftime("%m-%d %H:%M")


def fmt_dur(seconds):
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if m else f"{h}h"


# ---------------- Codeforces ----------------
def fetch_codeforces(session):
    """官方 API: https://codeforces.com/api/contest.list"""
    url = "https://codeforces.com/api/contest.list"
    data = session.get(url, headers=HEADERS, timeout=15).json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Codeforces API 返回异常: {data.get('comment')}")
    out = []
    for c in data["result"]:
        out.append({
            "platform": "CF",
            "name": c["name"],
            "start": c["startTimeSeconds"],
            "duration": c["durationSeconds"],
            "url": f"https://codeforces.com/contest/{c['id']}",
            "rated": c["phase"] not in ("BEFORE",),
            "phase": c["phase"],
        })
    return out


# ---------------- AtCoder ----------------
def _parse_atc_duration(s):
    """'01:40' -> 6000, '03:30' -> 12600, '240:00' -> 864000"""
    try:
        h, m = s.split(":")
        return int(h) * 3600 + int(m) * 60
    except Exception:
        return None


def _parse_atc_time(s):
    """'2026-09-27(Sun) 05:00' -> epoch (Asia/Tokyo)"""
    try:
        dt = datetime.strptime(s.strip(), "%Y-%m-%d(%a) %H:%M")
        return int(dt.replace(tzinfo=timezone(timedelta(hours=9))).timestamp())
    except Exception:
        return None


def fetch_atcoder_official(session):
    """直接抓取 AtCoder 官网 upcoming 表格 (最可靠, 含未来比赛)"""
    from html.parser import HTMLParser

    class _TableParser(HTMLParser):
        """解析 #contest-table-upcoming 表格行"""
        def __init__(self):
            super().__init__()
            self.in_upcoming = False
            self.in_row = False
            self.in_cell = False
            self.cur_cells = []
            self.cur_links = []
            self.rows = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "div" and attrs.get("id") == "contest-table-upcoming":
                self.in_upcoming = True
            elif self.in_upcoming and tag == "tr":
                self.in_row = True
                self.cur_cells = []
                self.cur_links = []
            elif self.in_row and tag == "td":
                self.in_cell = True
            elif self.in_cell and tag == "a":
                self.cur_links.append(attrs.get("href", ""))

        def handle_endtag(self, tag):
            if tag == "td" and self.in_cell:
                self.in_cell = False
            elif tag == "tr" and self.in_row:
                self.in_row = False
                if self.cur_cells:
                    self.rows.append((self.cur_cells[:], self.cur_links[:]))
            elif tag == "div" and self.in_upcoming:
                self.in_upcoming = False

        def handle_data(self, data):
            if self.in_cell:
                self.cur_cells.append(data.strip())

    url = "https://atcoder.jp/contests/?lang=en"
    html = session.get(url, headers=HEADERS, timeout=20).text
    p = _TableParser()
    p.feed(html)

    out = []
    for cells, links in p.rows:
        # 典型行: [start_time, contest_name, duration, rated_range]
        if len(cells) < 3:
            continue
        start = _parse_atc_time(cells[0])
        dur = _parse_atc_duration(cells[2])
        # 从链接提取 contest id
        cid = ""
        for lk in links:
            m = re.search(r"/contests/([\w-]+)", lk)
            if m:
                cid = m.group(1)
                break
        if not start:
            continue
        out.append({
            "platform": "ATC",
            "name": cells[1],
            "start": start,
            "duration": dur or 6000,
            "url": f"https://atcoder.jp/contests/{cid}" if cid else "https://atcoder.jp/contests/",
            "rated": len(cells) > 3 and cells[3] not in ("-", ""),
        })
    return out


def fetch_atcoder_kenkoooo(session):
    """kenkoooo 社区 API (仅已结束比赛, 作为 recent 补充)"""
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


def fetch_atcoder(session):
    """
    AtCoder 双通道抓取:
    1. 官网 upcoming 表格 (未来比赛)
    2. kenkoooo API (已结束比赛, 用于 recent 列表)
    两者合并去重
    """
    now = time.time()
    upcoming, errors = [], []

    # 通道 1: 官网
    try:
        upcoming = fetch_atcoder_official(session)
    except Exception as e:
        errors.append(f"官网抓取失败: {e}")

    # 通道 2: kenkoooo (只取已结束的, 避免和官网重复)
    past = []
    try:
        past = [c for c in fetch_atcoder_kenkoooo(session) if c["start"] < now]
    except Exception as e:
        errors.append(f"kenkoooo 抓取失败: {e}")

    # 合并去重 (按 start + name)
    seen = {(c["start"], c["name"]) for c in upcoming}
    merged = upcoming + [c for c in past if (c["start"], c["name"]) not in seen]

    if not merged:
        raise RuntimeError("AtCoder 双通道均失败: " + "; ".join(errors))
    return merged


# ---------------- 牛客 ----------------
def _deep_find_contests(node, found):
    """在 window.pageData JSON 里递归找长得像比赛的对象"""
    if isinstance(node, dict):
        if "contestName" in node and ("startTime" in node or "beginTime" in node):
            found.append(node)
        for v in node.values():
            _deep_find_contests(v, found)
    elif isinstance(node, list):
        for v in node:
            _deep_find_contests(v, found)


def fetch_nowcoder(session):
    """牛客 vip-index 页面内嵌 JSON (window.pageData)"""
    url = "https://ac.nowcoder.com/acm/contest/vip-index"
    html = session.get(url, headers={**HEADERS, "Referer": "https://ac.nowcoder.com/"},
                       timeout=15).text
    m = re.search(r"window\.pageData\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("牛客页面结构变化, 未找到 window.pageData")
    page = json.loads(m.group(1))
    found = []
    _deep_find_contests(page, found)
    out, seen = [], set()
    for c in found:
        name, cid = c.get("contestName"), c.get("contestId")
        if not name or cid in seen:
            continue
        seen.add(cid)
        st = c.get("startTime") or c.get("beginTime")  # "2026-09-25 19:00"
        et = c.get("endTime")
        start = int(time.mktime(time.strptime(st, "%Y-%m-%d %H:%M"))) - time.timezone \
            if isinstance(st, str) and re.match(r"\d{4}-\d{2}-\d{2}", st) else None
        duration = None
        if isinstance(et, str) and start:
            end = int(time.mktime(time.strptime(et, "%Y-%m-%d %H:%M"))) - time.timezone
            duration = end - start
        out.append({
            "platform": "NC",
            "name": name,
            "start": start,
            "duration": duration,
            "url": f"https://ac.nowcoder.com/acm/contest/{cid}",
            "rated": "Rated" in json.dumps(c, ensure_ascii=False),
        })
    return [o for o in out if o["start"]]


# ---------------- 洛谷 ----------------
def fetch_luogu(session):
    """洛谷 _contentOnly=1 返回 JSON"""
    url = "https://www.luogu.com.cn/contest/list?page=1&_contentOnly=1"
    data = session.get(url, headers={**HEADERS, "Referer": "https://www.luogu.com.cn/"},
                       timeout=15).json()
    contests = data["currentData"]["contests"]["result"]
    out = []
    for c in contests:
        out.append({
            "platform": "LG",
            "name": c["name"],
            "start": c["startTime"],
            "duration": c["endTime"] - c["startTime"],
            "url": f"https://www.luogu.com.cn/contest/{c['id']}",
            "rated": None,
        })
    return out


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


def filter_window(contests, upcoming_days, recent_days, now):
    upcoming = [c for c in contests if c["start"] and now - 1800 <= c["start"]
                <= now + upcoming_days * 86400]
    recent = [c for c in contests if c["start"]
              and now - recent_days * 86400 <= c["start"] < now]
    return upcoming, recent


def print_table(title, contests, tz, now=None):
    if not contests:
        print(f"\n== {title}: 无 ==\n")
        return
    print(f"\n== {title} ({len(contests)} 场) ==")
    print(f"{'平台':<4} {'开始时间':<12} {'时长':<8} 比赛")
    print("-" * 90)
    for c in sorted(contests, key=lambda x: x["start"]):
        line = (f"{c['platform']:<4} {fmt_time(c['start'], tz):<12} "
                f"{fmt_dur(c['duration'] or 0):<8} {c['name']}")
        if now:
            left = c["start"] - now
            if left > 0:
                d, rem = divmod(int(left), 86400)
                line += f"   ⏳ {d}天{rem // 3600}小时后"
        print(line)
    print()


def make_plan(upcoming, tz):
    """根据 upcoming 比赛生成一个简单的练习计划建议"""
    print("\n== 练习计划建议 ==")
    if not upcoming:
        print("未来没有检测到比赛, 建议: 打一场 VP(虚拟参赛) + 复习最近错题。")
        return
    by_day = defaultdict(list)
    for c in upcoming:
        day = datetime.fromtimestamp(c["start"], tz).date()
        by_day[day].append(c)
    for day in sorted(by_day):
        cs = by_day[day]
        names = "、".join(f"[{c['platform']}] {c['name']}" for c in cs)
        weekday = "一二三四五六日"[day.weekday()]
        print(f"\n📅 {day} (周{weekday}): {names}")
        if len(cs) >= 2:
            print("   → 当天多场比赛冲突: 优先打 Rated 且与你水平最接近的一场,")
            print("     另一场赛后补题(只做错得最多的 2~3 题)。")
        else:
            d = (datetime.fromtimestamp(cs[0]["start"], tz).date()
                 - datetime.now(tz).date()).days
            if d >= 3:
                print(f"   → 距比赛 {d} 天: 每天 2~3 道对应平台近期真题, 保持手感。")
            elif d >= 1:
                print("   → 明天/后天就比赛: 今天做一场短 VP 或回顾模板/错题, 早点休息。")
            else:
                print("   → 今天比赛: 赛前过一遍模板, 赛后 24h 内补完未做出的题。")


def export_ics(path, contests, tz):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//contest_tracker//CN"]
    for c in sorted(contests, key=lambda x: x["start"]):
        if not c["start"]:
            continue
        s = datetime.fromtimestamp(c["start"], tz)
        e = s + timedelta(seconds=c["duration"] or 2 * 3600)
        f = lambda d: d.strftime("%Y%m%dT%H%M%S")
        lines += ["BEGIN:VEVENT",
                  f"UID:{c['platform']}-{c['start']}@contest_tracker",
                  f"DTSTART;TZID=Asia/Shanghai:{f(s)}",
                  f"DTEND;TZID=Asia/Shanghai:{f(e)}",
                  f"SUMMARY:[{c['platform']}] {c['name']}",
                  f"URL:{c['url']}",
                  "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY",
                  "DESCRIPTION:Contest starting in 30 minutes", "END:VALARM",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))
    print(f"ICS 已导出: {path}")


# ---------------- HTML 仪表盘 ----------------
PLATFORM_META = {
    "CF":  {"name": "Codeforces", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)"},
    "ATC": {"name": "AtCoder",    "color": "#f59e0b", "bg": "rgba(245,158,11,.15)"},
    "NC":  {"name": "牛客",       "color": "#22c55e", "bg": "rgba(34,197,94,.15)"},
    "LG":  {"name": "洛谷",       "color": "#a855f7", "bg": "rgba(168,85,247,.15)"},
}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _card(c, tz):
    meta = PLATFORM_META.get(c["platform"], PLATFORM_META["CF"])
    start_str = datetime.fromtimestamp(c["start"], tz).strftime("%Y-%m-%d %H:%M")
    rated = " · Rated" if c.get("rated") else ""
    return f"""
    <div class="card" data-platform="{c['platform']}" data-start="{c['start']}">
      <div class="badge" style="color:{meta['color']};background:{meta['bg']}">{meta['name']}</div>
      <div class="card-name"><a href="{_esc(c['url'])}" target="_blank">{_esc(c['name'])}</a></div>
      <div class="card-meta">🕒 {start_str} &nbsp;·&nbsp; ⏱ {fmt_dur(c['duration'] or 0)}{rated}</div>
      <div class="countdown" data-start="{c['start']}"></div>
    </div>"""


def _render_fragments(upcoming, recent, tz):
    """返回 (upcoming_html, recent_html) 两个片段, 供整页渲染和局部刷新共用"""
    up_by_day, cards = defaultdict(list), []
    for c in sorted(upcoming, key=lambda x: x["start"]):
        up_by_day[datetime.fromtimestamp(c["start"], tz).date()].append(c)
    for day in sorted(up_by_day):
        wd = "一二三四五六日"[day.weekday()]
        cards.append(f'<div class="day-title">📅 {day} 周{wd} ({len(up_by_day[day])} 场)</div>')
        cards.append('<div class="grid">')
        cards.extend(_card(c, tz) for c in up_by_day[day])
        cards.append('</div>')
    upcoming_html = "\n".join(cards) if cards else '<p class="empty">近期没有检测到 upcoming 比赛</p>'

    recent_rows = "".join(
        f"<tr data-platform='{c['platform']}'>"
        f"<td><span class='dot' style='background:{PLATFORM_META[c['platform']]['color']}'></span>"
        f"{PLATFORM_META[c['platform']]['name']}</td>"
        f"<td>{datetime.fromtimestamp(c['start'], tz).strftime('%m-%d %H:%M')}</td>"
        f"<td>{_esc(c['name'])}</td>"
        f"<td><a href='{_esc(c['url'])}' target='_blank'>链接</a></td></tr>"
        for c in sorted(recent, key=lambda x: -x["start"]))
    recent_html = recent_rows or '<tr><td colspan="4" class="empty">无</td></tr>'
    return upcoming_html, recent_html


def render_html(upcoming, recent, tz, now, gen_time, frag=None):
    """生成自包含 HTML 仪表盘 (无外部依赖)"""
    upcoming_html, recent_html = frag or _render_fragments(upcoming, recent, tz)
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
  header {{ padding:24px 20px 8px; max-width:960px; margin:auto;
           display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  #refreshBtn {{ flex:none; background:#38bdf8; color:#0f172a; border:none;
                padding:8px 18px; border-radius:999px; font-size:14px; font-weight:600;
                cursor:pointer; }}
  #refreshBtn:disabled {{ opacity:.5; cursor:default; }}
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
  <div>
    <h1>🏁 比赛信息看板</h1>
    <div class="sub" id="genTime">Codeforces · AtCoder · 牛客 · 洛谷 ｜ 生成时间: {gen_time}</div>
  </div>
  <button id="refreshBtn" onclick="refreshData()">🔄 刷新数据</button>
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
<footer>由 contest_tracker.py 生成 · 点右上角按钮或重新运行脚本即可刷新数据</footer>
<script>
// 平台筛选 (事件委托, 刷新后无需重新绑定)
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
// 倒计时
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
// 一键刷新: 需要 --serve 模式, 由本地服务器重新抓取四平台数据
async function refreshData() {{
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '⏳ 刷新中…';
  try {{
    const r = await fetch('/api/refresh', {{ cache: 'no-store' }});
    if (!r.ok) throw new Error('http ' + r.status);
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'unknown');
    document.getElementById('upcoming').innerHTML = d.upcoming_html;
    document.getElementById('recent').innerHTML = d.recent_html;
    document.getElementById('genTime').textContent =
      'Codeforces · AtCoder · 牛客 · 洛谷 ｜ 生成时间: ' + d.gen;
    const active = document.querySelector('.chip.active');
    applyFilter(active ? active.dataset.p : 'ALL');
    tick();
  }} catch (e) {{
    alert('⚠️ 刷新失败: 当前是静态页面, 无法直接抓取比赛数据。\\n\\n'
        + '请改用服务模式启动:\\n'
        + '    python contest_tracker.py --serve\\n\\n'
        + '然后打开 http://localhost:8000 再点刷新。\\n(原始错误: ' + e.message + ')');
  }} finally {{
    btn.disabled = false; btn.textContent = old;
  }}
}}
</script>
</body>
</html>"""


# ---------------- 网页服务模式 ----------------
def serve_dashboard(port, args):
    """本地起服务, 打开页面后点按钮即可重新抓取数据并局部刷新"""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import urllib.parse

    tz = TZ_SHANGHAI

    def build():
        now = time.time()
        contests, errors = collect(args.platforms)
        upcoming, recent = filter_window(contests, args.upcoming, args.recent, now)
        gen = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        frag = _render_fragments(upcoming, recent, tz)
        page = render_html(upcoming, recent, tz, now, gen, frag=frag)
        return page, frag, gen, errors

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="text/html; charset=utf-8"):
            b = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/refresh":
                try:
                    _, frag, gen, errors = build()
                    payload = {"ok": True, "upcoming_html": frag[0],
                               "recent_html": frag[1], "gen": gen,
                               "errors": errors}
                except Exception as e:
                    payload = {"ok": False, "error": str(e)}
                self._send(json.dumps(payload, ensure_ascii=False),
                           "application/json; charset=utf-8")
            else:
                page, _, _, errors = build()   # 每次打开页面也抓最新数据
                self._send(page)

    print(f"看板已启动 → http://localhost:{port}  (按 Ctrl+C 停止)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description="比赛信息聚合工具 (CF/ATC/牛客/洛谷)")
    ap.add_argument("-u", "--upcoming", type=int, default=14, help="upcoming 范围(天), 默认14")
    ap.add_argument("-r", "--recent", type=int, default=7, help="recent 范围(天), 默认7")
    ap.add_argument("-p", "--platforms", nargs="+",
                    choices=list(FETCHERS), default=list(FETCHERS), help="选择平台")
    ap.add_argument("--json", metavar="FILE", help="导出 JSON")
    ap.add_argument("--ics", metavar="FILE", help="导出 ICS 日历(含赛前30分钟提醒)")
    ap.add_argument("--html", metavar="FILE", help="生成 HTML 仪表盘网页")
    ap.add_argument("--plan", action="store_true", help="输出练习计划建议")
    ap.add_argument("--serve", action="store_true", help="启动网页服务(页内按钮一键刷新)")
    ap.add_argument("--port", type=int, default=8000, help="服务端口, 默认8000")
    args = ap.parse_args()

    tz = TZ_SHANGHAI
    now = time.time()

    if args.serve:
        serve_dashboard(args.port, args)
        return

    contests, errors = collect(args.platforms)
    for p, err in errors.items():
        print(f"[警告] {p} 抓取失败: {err}", file=sys.stderr)

    upcoming, recent = filter_window(contests, args.upcoming, args.recent, now)

    print_table(f"未来 {args.upcoming} 天的比赛", upcoming, tz, now=now)
    print_table(f"最近 {args.recent} 天已结束的比赛", recent, tz)

    if args.plan:
        make_plan(upcoming, tz)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump({"upcoming": upcoming, "recent": recent},
                      fp, ensure_ascii=False, indent=2)
        print(f"JSON 已导出: {args.json}")

    if args.ics:
        export_ics(args.ics, upcoming, tz)

    if args.html:
        gen = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        with open(args.html, "w", encoding="utf-8") as fp:
            fp.write(render_html(upcoming, recent, tz, now, gen))
        print(f"HTML 仪表盘已生成: {args.html}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
比赛信息抓取脚本（含 ICS 日历导出）
抓取 Codeforces / AtCoder / 牛客 / 洛谷 的即将开始的比赛
用法: pip install requests beautifulsoup4
      python contest_scraper.py
输出: contests.json  +  contests.ics（可导入/订阅到飞书等日历）
"""
import re
import json
import time
import hashlib
import datetime as dt
from html import unescape

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
NOW = time.time()


def to_iso(ts):
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


# ---------------- Codeforces: 官方 API ----------------
def fetch_codeforces():
    url = "https://codeforces.com/api/contest.list"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    assert data["status"] == "OK", data.get("comment")
    out = []
    for c in data["result"]:
        if c["phase"] == "BEFORE" and c.get("startTimeSeconds", 0) > NOW:
            out.append({
                "source": "Codeforces",
                "name": c["name"],
                "start": to_iso(c["startTimeSeconds"]),
                "duration_min": c["durationSeconds"] // 60,
                "url": f"https://codeforces.com/contest/{c['id']}",
            })
    return out


# ---------------- AtCoder: 解析 upcoming 表格 ----------------
def fetch_atcoder():
    url = "https://atcoder.jp/contests/?lang=en"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    div = soup.select_one("#contest-table-upcoming")
    if not div:
        return out
    tz = dt.timezone(dt.timedelta(hours=9))  # AtCoder 使用 JST
    for tr in div.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        a = tds[1].find("a")
        if not a:
            continue
        start = dt.datetime.strptime(tds[0].text.strip(), "%Y-%m-%d %H:%M:%S%z") \
            if "%z" in tds[0].text else dt.datetime.strptime(
                re.sub(r"\+\d{4}$", "", tds[0].text.strip()),
                "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        dur = tds[2].text.strip()  # e.g. 01:40
        h, m = dur.split(":")
        out.append({
            "source": "AtCoder",
            "name": a.text.strip(),
            "start": start.astimezone().isoformat(timespec="minutes"),
            "duration_min": int(h) * 60 + int(m),
            "url": "https://atcoder.jp" + a["href"],
        })
    return out


# ---------------- 牛客: 解析 vip-index 页面 ----------------
def fetch_nowcoder():
    url = "https://ac.nowcoder.com/acm/contest/vip-index"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    html = r.text
    out, seen = [], set()
    # 比赛链接形如 /acm/contest/数字，后面跟随报名时间/比赛时间信息
    for m in re.finditer(r'href="(/acm/contest/(\d+))"[^>]*>(.*?)</a>', html, re.S):
        cid, title = m.group(2), unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        if not title or cid in seen:
            continue
        tail = html[m.end():m.end() + 600]
        tm = re.search(r"比赛时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*至\s*"
                       r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", tail)
        if not tm:  # 已结束的比赛没有紧跟的报名/比赛时间块，跳过
            continue
        start = dt.datetime.strptime(tm.group(1), "%Y-%m-%d %H:%M")
        end = dt.datetime.strptime(tm.group(2), "%Y-%m-%d %H:%M")
        if start.timestamp() < NOW:
            continue
        seen.add(cid)
        out.append({
            "source": "牛客",
            "name": title,
            "start": start.isoformat(timespec="minutes"),
            "duration_min": int((end - start).total_seconds()) // 60,
            "url": "https://ac.nowcoder.com/acm/contest/" + cid,
        })
    return out


# ---------------- 洛谷: 解析比赛列表页 ----------------
def fetch_luogu():
    url = "https://www.luogu.com.cn/contest/list"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    html = r.text
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="/contest/(\d+)"[^>]*>(.*?)</a>', html, re.S):
        cid, title = m.group(1), unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if not title or cid in seen:
            continue
        tail = html[m.end():m.end() + 800]
        tm = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})", tail)
        if not tm:
            continue
        start = dt.datetime.strptime(tm.group(1), "%Y-%m-%d %H:%M")
        if start.timestamp() < NOW:
            continue
        seen.add(cid)
        out.append({
            "source": "洛谷",
            "name": title,
            "start": start.isoformat(timespec="minutes"),
            "duration_min": None,  # 结束时间可能跨天，前端可只展示开始时间
            "url": "https://www.luogu.com.cn/contest/" + cid,
        })
    return out


FETCHERS = {
    "Codeforces": fetch_codeforces,
    "AtCoder": fetch_atcoder,
    "牛客": fetch_nowcoder,
    "洛谷": fetch_luogu,
}


# ================= ICS 导出 =================
# 说明:
#   - 所有时间统一转成"本地浮动时间"(不带 Z 后缀), 导入日历时按本地时区显示,
#     因为抓取的 start 已经按本地时间展示, 这样最直观、不会错位
#   - UID 由 url 生成, 稳定且唯一, 重复导入同一文件会更新而非新建

def ics_escape(text):
    """转义 ICS 文本字段中的特殊字符"""
    return (str(text).replace("\\", "\\\\")
                     .replace(";", "\\;")
                     .replace(",", "\\,")
                     .replace("\n", "\\n"))


def ics_fold(line):
    """按 75 字节折行(中文占多字节), 续行前加空格"""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:  # 74 + 续行空格 = 75
            parts.append(cur)
            cur = b
        else:
            cur += b
    parts.append(cur)
    return "\r\n ".join(p.decode("utf-8") for p in parts)


def to_local_naive(iso_str):
    """ISO 字符串 -> 本地 naive datetime(不带时区)"""
    d = dt.datetime.fromisoformat(iso_str)
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)  # 转成本地时间再去掉时区
    return d


def export_ics(contests, path="contests.ics", default_duration_min=120):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//contest-scraper//CN",
        "CALSCALE:GREGORIAN",
    ]
    for c in contests:
        start = to_local_naive(c["start"])
        dur = c.get("duration_min") or default_duration_min
        end = start + dt.timedelta(minutes=dur)
        uid_src = c.get("url") or f"{c['source']}-{c['name']}-{c['start']}"
        uid = hashlib.md5(uid_src.encode("utf-8")).hexdigest() + "@contest-scraper"

        lines += [
            "BEGIN:VEVENT",
            ics_fold(f"UID:{uid}"),
            f"DTSTAMP:{dt.datetime.now():%Y%m%dT%H%M%S}",
            f"DTSTART:{start:%Y%m%dT%H%M%S}",
            f"DTEND:{end:%Y%m%dT%H%M%S}",
            ics_fold(f"SUMMARY:[{c['source']}] {ics_escape(c['name'])}"),
            ics_fold(f"DESCRIPTION:{ics_escape('比赛链接: ' + c.get('url', ''))}"),
            "BEGIN:VALARM",
            "TRIGGER:-PT30M",       # 提前 30 分钟提醒
            "ACTION:DISPLAY",
            "DESCRIPTION:比赛即将开始",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    print(f"已导出 ICS 日历: {path} ({len(contests)} 场)")


if __name__ == "__main__":
    all_contests = []
    for name, fn in FETCHERS.items():
        try:
            res = fn()
            print(f"[OK] {name}: {len(res)} 场即将开始")
            all_contests.extend(res)
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
    all_contests.sort(key=lambda x: x["start"])
    print(f"\n共 {len(all_contests)} 场比赛:\n")
    for c in all_contests:
        dur = f"{c['duration_min']}min" if c["duration_min"] else "?"
        print(f"[{c['source']:<12}] {c['start']}  (时长 {dur})\n    {c['name']}\n    {c['url']}\n")
    with open("contests.json", "w", encoding="utf-8") as f:
        json.dump(all_contests, f, ensure_ascii=False, indent=2)
    print("已保存到 contests.json")

    # 新增: 导出 ICS 日历文件
    export_ics(all_contests)

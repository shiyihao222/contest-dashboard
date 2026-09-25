# -*- coding: utf-8 -*-
"""
比赛信息抓取验证脚本
抓取 Codeforces / AtCoder / 牛客 / 洛谷 的即将开始的比赛
用法: pip install requests beautifulsoup4
      python contest_scraper.py
"""
import re
import json
import time
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

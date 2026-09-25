# Contest Tracker

定时抓取 Codeforces / AtCoder / 牛客 的 upcoming 比赛，生成 `contests.json`，通过 GitHub 托管供网站调用。

## 数据接口（给前端用）

```
https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/contests.json
```

返回格式：

```json
{
  "updated_at": "2026-09-25T17:30:00+08:00",
  "failed_sources": [],
  "contests": [
    {
      "source": "Codeforces",
      "name": "Codeforces Round 1123 (Div. 2)",
      "start": "2026-09-25T22:35+08:00",
      "duration_min": 135,
      "url": "https://codeforces.com/contest/2267"
    }
  ]
}
```

## 前端调用示例

```js
fetch("https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/contests.json")
  .then(r => r.json())
  .then(data => {
    const upcoming = data.contests.filter(
      c => new Date(c.start) > new Date()
    );
    // 渲染 upcoming ...
  });
```

## 注意事项

- Actions 的 cron 是 UTC 时间，且高峰期可能延迟 5-15 分钟，对比赛信息足够
- raw.githubusercontent.com 有 5 分钟缓存，与 30 分钟抓取间隔匹配
- 国内访问 raw 较慢的话，可换 jsdelivr CDN：
  `https://cdn.jsdelivr.net/gh/<用户名>/<仓库>@main/contests.json`
- 牛客/AtCoder 是 HTML 解析，页面改版会导致 `[FAIL]`，workflow 会显示告警

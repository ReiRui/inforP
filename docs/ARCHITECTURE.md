# inforP 技术架构

## 1. 总体架构

```text
Scheduler
  -> Pipeline
    -> Source Loader
    -> Collectors
      -> LLM Remote Search
      -> RSS
      -> Web Page
      -> Browser Profile
      -> YouTube Transcript
    -> Normalizer
    -> Event Extractor
    -> Verifier
    -> Ranker
    -> Report Builder
    -> Delivery
    -> Metrics Recorder
```

主流程是确定性的 workflow。LLM 不控制调度和状态，只在以下节点中发挥作用：

- 远程搜索。
- 事件抽取。
- 补充核验。
- 找反证。
- 摘要和报告生成辅助。
- 动态信源评估。

## 1.1 当前部署策略：DeepSeek API（本地检索 + 本地结构化）

LLM 层使用 DeepSeek 官方 API，网络可达，不需要本地翻墙或代理网关。职责划分：

- **DeepSeek Search API**（`POST /v1/tools/search`）：按关键词检索，返回 `url` / `title` / `summary` / `date` / `authority`，检索结果可追溯，构成 `raw_items` 的一部分。
- **`deepseek-flash` + `text.format: json_schema`**：关键词规划（SearchPlan）、事件抽取（LLMEventBatch）、核验与反证（LLMEventBatch）。严格结构化输出，避免解析不稳。
- **本地层**：配置、固定源、SQLite、Markdown 报告、SMTP 邮件、企微摘要、运行统计。

约束（2026-11 官方文档 + 实测）：`deepseek_web_search` / `deepseek_read_url` 与 `text.format` 互斥，同时传会返回 400 `only 'text' is supported together with deepseek_web_search`。因此本项目**不在模型调用里开启联网工具**，而是先用 Search API 取结果，再做结构化。

计费：模型按 token（Flash 高峰输入 2 元/百万、输出 8 元/百万，空闲时段减半）；Search / Fetch API 按成功请求 5 元/千次，由模型服务端工具触发的搜索自 2026-11-18 12:00 起不再单独计费。费用从 DeepSeek 账户充值/赠送余额直接扣减。

## 2. 模块划分

```text
src/inforp/
  cli.py                命令行入口
  config.py             配置加载
  schemas.py            结构化数据模型
  store.py              数据库模型与持久化
  pipeline.py           日报/周报主流程
  llm.py                LLM 搜索与结构化输出适配器
  verification.py       置信度与排序规则
  source_manager.py     信源评分、候选源、升降级
  report.py             Markdown 报告生成
  delivery.py           邮件、企业微信、本地文件推送
  scheduler.py          北京时间调度
  collectors/
    base.py
    rss.py
    llm_search.py
    browser_profile.py
```

## 3. 数据模型

核心表：

- `sources`：信源档案。
- `raw_items`：原始采集条目。
- `events`：归并后的事件。
- `reports`：日报/周报。
- `run_metrics`：运行统计。
- `source_metrics`：信源动态分数。

## 4. 存储

V1 默认使用 SQLite：

```env
DATABASE_URL=sqlite:///data/inforp.db
```

后续可以无代码修改切换到 Postgres：

```env
DATABASE_URL=postgresql+psycopg://user:password@host:5432/inforp
```

## 5. 调度

V1 使用 APScheduler：

- 工作日 09:00：日报。
- 周一 09:08：周报。

后续升级可以替换为 Temporal，以获得更强的 durable execution、重试、可视化和长任务恢复能力。

## 6. LLM 远程搜索

LLM 搜索输入由 `configs/sources.yaml` 中的 `llm_search.daily_queries` 和 `llm_search.weekly_queries` 控制。

输出必须符合结构化 schema：

```json
{
  "events": [
    {
      "title": "...",
      "summary": "...",
      "claims": ["..."],
      "topic": "ai_tech",
      "importance_score": 0.91,
      "confidence": "high",
      "source_urls": ["..."],
      "verification_notes": "...",
      "contradictions": [],
      "tags": ["model_release"]
    }
  ]
}
```

## 7. 核验策略

核验分为工程规则和 LLM 辅助判断：

- 官方/论文/代码/监管文件优先。
- 社交媒体、论坛、群聊只作为线索。
- 重要事件需要原始来源或两个以上独立来源。
- AI 低置信度事件过滤。
- 综合低置信度事件进入 `待观察`。
- 事件被证伪或修正时记录到后续报告。

### 7.1 信源分级与独立来源计数

每个信源在 `configs/sources.yaml` 里有 `tier`：

| tier | 含义 | 典型来源 |
|---|---|---|
| `T1` | 官方一手 | 公司新闻室、监管机构、官方文档 |
| `T1_5` | 准官方 / 研究 | 官方工程博客、研究机构、论文库、学术媒体 |
| `T2` | 媒体 / 通讯 / 社交 / 论坛 | 只作发现与线索，不作为确认源 |

排序与置信度用到两个信号（`verification.py`）：

- **独立来源数**：按**域名**去重，不按 URL 数。同一份新闻稿被五家转载，只算一个来源。
- **最强来源分级**：事件引用到的域名里取最强的一档。

由此产生两条规则：

- 只有单一 `T2` 来源却标了 `confidence=high` 的事件，自动下调为 `medium`，并在核验说明里注明原因。
- 入选项达到 3 个独立域名打 `多源确认` 标签，只有 1 个打 `单一来源` 标签。
- 排序加分：独立来源每多一个 +0.04（上限 0.16）；`T1` +0.06、`T1_5` +0.03。

这套做法参考了开源项目 AIHOT 的热度口径：热度按事件算而不是按文章算，同一来源重复报道不重复计分。

### 7.1.1 重要事件不因「待核验」被挤出正文

模型会把「结论尚未被共同体接受」的重大事件标成 `is_watchlist`，而正文小节只收 `is_watchlist=false` 的事件——结果就是黎曼猜想这类全周最重要的新闻被塞进观察池。

`promote_important_watchlist()` 处理这个区分：`importance_score >= WATCHLIST_PROMOTE_IMPORTANCE`（默认 0.85）且 `confidence != low` 的事件，即使被标为观察项也回到正文，加 `待观察` 标签，接受度问题留在核验说明与分歧/反证里。重要性和结论确定性是两个维度，不能互相替代。

### 7.1.2 外发开关

`INFORP_NO_OUTBOUND=1` 会让邮件、企微、企微周报摘要三个渠道全部变成 no-op（返回 `skipped`），报告仍然写入 `REPORT_OUTPUT_DIR`。构造函数直接读环境变量，所以 CLI 手工路径也覆盖。

`inforp run-daily --dry-run` / `run-weekly --dry-run` 会自动设置它；`inforp render-weekly-wecom-summary <report.md>` 只渲染企微文本，完全不碰网络。

## 7.2 采集窗口

固定源并发抓取（`COLLECT_CONCURRENCY`），抓完按三件事收缩：

- 只保留 `COLLECT_LOOKBACK_HOURS`（默认 48 小时）内的条目，无日期的条目保留；
- 每个源最多保留 `COLLECT_MAX_ITEMS_PER_SOURCE`（默认 20）条；
- 按发布时间倒序，让抽取调用先看到最新材料。

原因：RSS 单个 feed 可能一次返回 50 条，不做收缩会把 36 小时的选题窗口冲垮。

## 7.3 报告体积控制

数据库保留**全部** ranked 事件，只有报告正文受限：

- `MAX_REPORT_EVENTS`（默认 15）：日报正文最多几条事件。
- `MAX_WEEKLY_REPORT_EVENTS`（默认 25）：周报正文上限。
- `MAX_WATCHLIST_EVENTS`（默认 8）：观察池上限。
- 溢出的条目数记在 `run_metrics.metadata.events_overflow`，报告头部也会显示。

抽取侧另有 `DEEPSEEK_MAX_RAW_ITEMS_PER_CALL`（默认 120）和 `DEEPSEEK_RAW_ITEM_CHARS`（默认 2500）控制单次抽取的输入规模；条目按发布时间倒序，先送最新的。

## 8. 登录态采集器

登录态采集器采用隔离的 Playwright profile：

```text
profiles/
  zhihu/
  nga/
  wechat_public/
```

流程：

1. 系统打开浏览器。
2. 用户手动完成登录或验证。
3. 系统保存 profile。
4. 后续使用该 profile 访问用户授权页面。

该模块默认关闭：

```env
ENABLE_BROWSER_CONNECTORS=false
```

## 9. 动态信源评分

每个信源维护动态指标：

- `mentions`：被采集或引用次数。
- `confirmed`：被确认次数。
- `corrected`：被修正次数。
- `false_reports`：被证伪次数。
- `avg_importance`：平均重要性。
- `score`：动态可靠性分数。

评分公式 V1：

```text
score =
  base_reliability
  + confirmed_rate * 0.25
  + avg_importance * 0.10
  - false_rate * 0.35
  - correction_rate * 0.15
```

## 10. 推送

V1 支持：

- `local_file`：写入 `reports/`。
- `email`：SMTP 邮件。
- `wecom`：企业微信机器人 webhook。

报告正文不直接依赖推送渠道，所有渠道共用同一个 Markdown 内容。

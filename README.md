# inforP

个人情报推送系统：按北京时间在工作日 09:00 生成日报，并在周一额外生成上周周报。系统聚焦两类信息：

- AI 技术突破、模型、论文、开源、产品与应用。
- AI 硬件、GPU、服务器、芯片、通信、数据中心与算力供应链。
- 国际政治、经济、中国政策、泛 AI 产业与其他重要综合信息。

设计原则：

- 调度、采集、存储、推送走确定性流水线。
- LLM 负责远程搜索、补充检索、跨源核验、摘要和结构化输出。
- AI 低置信度消息默认不推送，只入库留痕。
- 综合信息低置信度消息可以进入报告，但必须标记为 `待观察`。
- 所有被推送条目必须保留来源、置信度和核验说明。

> **关于示例配置**：仓库里的信源、关注方向和报告模板是可复制的示例。其中 EDA / 半导体 IP 只是用来演示「如何按行业定制选题权重」的一个示例方向，与任何具体公司无关；换成你自己的行业即可。

许可证：[MIT](LICENSE)。

## 文档

- [PRD](docs/PRD.md)
- [技术架构](docs/ARCHITECTURE.md)
- [V1 任务拆分](docs/V1_TASKS.md)
- [报告生成规范](docs/REPORT_GUIDELINES.md)

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
# configs/sources.yaml 已随仓库提供（80+ 信源），按需增删即可；
# 想要空白模板就从 configs/sources.example.yaml 开始
.\.venv\Scripts\inforp.exe init-db
.\.venv\Scripts\inforp.exe run-daily
```

调度运行：

```powershell
.\.venv\Scripts\inforp.exe schedule
```

定时任务用 Windows 任务计划，注册脚本是 `automation\register_windows_task.ps1`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File automation\register_windows_task.ps1
```

注册后任务在工作日 09:00 运行 `automation\run_pipeline_daily.ps1`：每个工作日跑 `inforp run-daily` 生成日报，周一额外跑 `inforp run-weekly` 生成周报，邮件由管线按 `.env` 的 `DELIVERY_CHANNELS` / `EMAIL_RECIPIENT_SET` 投递。脚本以「报告是否落盘」判定成败，失败会记事故日志并以非零状态退出。

注册脚本优先使用 `pwsh`；没有时回退到 `powershell.exe -EncodedCommand`，并在入口用 `Get-Content -Encoding UTF8` 读取脚本，避免 Windows PowerShell 5.1 用本地代码页解析 UTF-8 `.ps1` 导致中文字符串损坏——不要改回裸 `powershell.exe -File`。

## 核心配置

`.env` 中至少需要配置：

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_VERIFIER_MODEL=deepseek-flash
DATABASE_URL=sqlite:///data/inforp.db
TIMEZONE=Asia/Shanghai
INCIDENT_LOG_FILE=logs/automation_incidents.jsonl
```

API Key 只写在项目根目录的 `.env` 里（该文件已被 `.gitignore` 忽略），代码不读取其他位置的密钥。

推送渠道可以按需启用：

```env
DELIVERY_CHANNELS=local_file,email,wecom
```

> `local_file` 表示把报告写成 `reports/` 下的本地 Markdown 文件，不涉及任何外部服务。

```env
WECOM_WEBHOOK_URL=...
WECOM_WEEKLY_SUMMARY_TOP_ITEMS=10
SMTP_HOST=...
SMTP_USER=...
SMTP_PASSWORD=...
IMAP_HOST=...
# IMAP_SENT_COPY_ENABLED=true
EMAIL_TO=...
RECIPIENTS_FILE=configs/recipients.yaml
# EMAIL_RECIPIENT_SET=company_colleagues
```

收件人清单放在 `configs/recipients.yaml`。该文件**不进版本库**（含同事邮箱），仓库里只有示例：

```powershell
Copy-Item configs\recipients.example.yaml configs\recipients.yaml
# 然后把示例里的占位地址换成真实收件人
```

两个集合分工固定：`test_only` 只放你自己，测试邮件只发这里；`company_colleagues` 是正式日报/周报收件人。测试邮件示例：

```powershell
.\.venv\Scripts\inforp.exe send-report-email reports\2026-08-31_daily_remote.md --recipient-set test_only
```

正式日报/周报才发送到 `company_colleagues`。除非用户明确要求补发，不要对正式同事组重复发送同一份报告。

## 事故日志

自动化、日报/周报管线、邮件和企微发送中的异常会记录到两处：

- `logs/automation_incidents.jsonl`：UTF-8 JSONL，方便直接排查和备份。
- `data/inforp.db` 的 `automation_incidents` 表：方便 CLI 查询和后续统计。

查看最近事故：

```powershell
.\.venv\Scripts\inforp.exe incidents --limit 10
```

手动补记事故：

```powershell
.\.venv\Scripts\inforp.exe log-incident "自动化运行失败" "示例：抓取超时" --component automation.pipeline --severity error --status mitigated
```

## 当前运行策略

LLM 层已从 OpenAI 切换到 DeepSeek 官方 API（`https://api.deepseek.com`，OpenAI 兼容的 Responses 接口），`ENABLE_LLM=true`。

分层方式：

- **检索**：DeepSeek Search API（`POST /v1/tools/search`）按关键词检索，返回真实 URL、站点名、发布时间和权威度分数；检索是确定性的，不在模型里黑箱发生。
- **抽取与核验**：`deepseek-flash` 通过 `text.format` 的 `json_schema` 严格结构化输出，负责关键词规划、事件抽取、跨源核验和反证说明。
- **本地**：固定源抓取、SQLite、Markdown、SMTP 邮件、企微摘要。

注意：DeepSeek 的联网工具（`deepseek_web_search`）不能与结构化 JSON 输出同时使用，会返回 400，因此本项目刻意不在模型调用里开联网工具，而是先用 Search API 取回结果再交给模型。相关约束见 [技术架构](docs/ARCHITECTURE.md)。

检索能力边界（2026-11 实测）：GitHub、Hacker News、arXiv、Reuters、知乎、微博、微信公众号、B 站可检索；**X/Twitter、Reddit、Telegram、Discord 检索不到**，`site:x.com` 之类的过滤返回空或无关结果。相关信息只能靠固定源或人工补入。

## 关注方向（可配置）

代码里不写死任何行业。`configs/sources.yaml` 顶部的 `profile` 决定「哪些议题要加权」：

```yaml
profile:
  focus_label: 示例行业（请替换）
  focus_directions:
    - 示例方向一
    - 示例方向二
```

- `focus_label`：行业名，只用于提示词表述。
- `focus_directions`：关注方向列表，会被注入抽取提示词，作为提高重要性权重的依据。

改这里就能把同一套引擎指向另一个行业，不需要动 Python。仓库自带的示例值只是占位，**请替换成你自己关心的方向**。

## 信源维护

信源在 `configs/sources.yaml`，每个源带 `tier`：

- `T1` 官方一手（公司新闻室、监管机构、官方文档）
- `T1_5` 准官方与研究（官方工程博客、研究机构、论文库、学术媒体）
- `T2` 媒体 / 通讯 / 社交 / 论坛——只作发现与线索，不作为确认源

维护规则：

1. **优先 RSS**。列表页整页抓取只能拿到一个页面文本，RSS 逐条带发布时间，质量差一个量级。官方页面没有 RSS 时先找该站的 feed（`/rss.xml`、`/feed`、`/feed.xml`、`/rss`、`/atom.xml`）。
2. **抓不到就删**。超时、403、404、JS 渲染的空壳页（抓回来只有几百字符导航文字）不要留占位——它们会污染抽取结果。这类站点交给 `llm_search` 检索层覆盖。
3. **加源前先实测**：能取到条目、最新条目日期在合理范围内，再写进去。

2026-11 这一轮清理：删掉 34 个失效/空壳源，5 个列表页改成 RSS（OpenAI、DeepMind、NVIDIA 新闻室、Latent Space、量子位），新增 22 个高价值源（Arm 新闻室、Meta Engineering、AWS ML、GitHub Blog、SemiEngineering、BAIR、Science、MIT Tech Review、The Verge、TechCrunch、Ars Technica、WIRED、Tom's Hardware、ServeTheHome、Hacker News、Simon Willison、Interconnects、One Useful Thing、InfoQ 中文、Solidot、雷锋网）。

仓库自带的配置可直接用，也可以按上面的规则换成你自己的信源。

## 本地测试注意

跑 `run-weekly` 做测试时，**必须**同时关掉企微摘要：

```env
WECOM_WEEKLY_SUMMARY_ENABLED=false
```

原因：企微周报摘要由 `WECOM_WEEKLY_SUMMARY_ENABLED` 单独控制，**不受 `DELIVERY_CHANNELS` 约束**。只改 `DELIVERY_CHANNELS=local_file` 仍会在周报生成完成后自动发一条企微群消息。

另外，企微 webhook 消息发出后**无法撤回**，测试前务必确认这行已关。

## 登录类平台接入边界

系统支持保存你授权后的浏览器登录态，用于读取你有权访问的页面。手机号、扫码、验证码、实名、人机验证等步骤需要你手动完成。系统不会实现批量注册、绕过风控、突破访问控制或规避平台限制。

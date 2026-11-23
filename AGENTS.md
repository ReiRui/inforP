# inforP Agent Notes

- User-facing messages for this project must be Simplified Chinese.
- The LLM layer uses the DeepSeek official API (`https://api.deepseek.com`) with `DEEPSEEK_API_KEY` from the project `.env`. Do not re-add OpenAI credentials, local proxies, or `OPENAI_BASE_URL` indirection.
- DeepSeek `deepseek_web_search` / `deepseek_read_url` cannot be combined with `text.format` JSON schema (HTTP 400). Keep retrieval in the Search API and structured output in a tool-free call.
- DeepSeek search does not index X/Twitter, Reddit, Telegram, or Discord (`site:` filters return empty or unrelated results). Treat those channels as uncovered: use fixed sources or manual input, and never claim coverage for them.
- For inaccessible or cross-border research, use Codex/LLM remote search and keep source verification strict.
- Use configs/sources.yaml source tiers: official, paper, media, newsletter, social, forum.
- High-priority items need a primary source or two reliable independent sources. Otherwise mark them as watchlist leads.
- Prefer Chinese original texts for China policy items.
- Raise priority for EDA, semiconductor IP, chip design flow, verification, physical design, PDK/design rules, layout-design protection, EDA/IP licensing, export controls, litigation, M&A, and domestic substitution. Do not lower verification thresholds.
- Do not limit important AI coverage to model or product launches. Major AI application breakthroughs in science and engineering, including mathematics, formal proof, physics, fluid dynamics, weather/climate, materials, chemistry, biology, drug discovery, robotics, simulation, optimization, control, manufacturing, compilers, software engineering, and EDA-adjacent scientific computing are high-priority candidates.
- For recognized-hard-problem claims, distinguish the fact that a claim/proof/result was published from community acceptance, reproducibility, credit disputes, and downstream impact.
- Any pipeline test run must set `WECOM_WEEKLY_SUMMARY_ENABLED=false`. The weekly WeCom summary is gated by that flag alone and ignores `DELIVERY_CHANNELS`; forgetting it posts a real group message that cannot be recalled.
- Test email may only go to the test_only recipient set. Formal daily/weekly reports go to company_colleagues and must not be sent repeatedly unless explicitly requested.
- Keep automation incident details in logs/automation_incidents.jsonl and the automation_incidents table. Do not put automation errors in daily/weekly report content unless explicitly requested.
- Windows scheduled task scripts must be ASCII-safe or loaded explicitly as UTF-8. Do not add non-ASCII strings to .ps1 automation wrappers unless the registered task action reads the script with Get-Content -Encoding UTF8 before execution.

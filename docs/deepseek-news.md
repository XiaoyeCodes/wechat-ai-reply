# 原生新闻技能与 DeepSeek 搜索

## Aria 使用上游原版新闻技能

来源：[cclank/news-aggregator-skill](https://github.com/cclank/news-aggregator-skill)，固定提交 `b639b622a79ec242669d6111708efbf863592de4`。

`skills/news-aggregator-skill` 中的 SKILL.md、采集脚本、预设说明和模板共 18 个文件保持上游原样，SHA-256 清单见 `upstream.json`。Aria 绑定此技能，已取消之前的澎湃专用采集、来源限制和固定 1–5 条模板。

### 使用示例

- “汇总5条今天的重要新闻。”
- “今天只看国际新闻，给我3条。”
- “整理经济和民生新闻，不要娱乐八卦。”
- “汇总最新 AI 技术动态。”
- “使用 finance 预设生成财经早报。”
- “如意如意”（查看原版交互菜单）。

类别、条数和关键词由本轮请求决定。模型选择原版来源或早报预设，读取脚本实际返回的数据，再按原版模板用中文生成报告。原版的来源、时间、链接和深读机制均保留。普通聊天不自动运行新闻脚本。

## 执行入口

```mermaid
flowchart LR
  A[用户询问新闻] --> B[加载原版 SKILL.md]
  B --> C[Aria 选择来源 关键词 或预设]
  C --> D[news_collect 执行原版 Python 脚本]
  D --> E[原始 JSON 与深读结果交给 Aria]
  E --> F[按原版模板汇总并保存报告]
```

工作台只提供 `news_collect` 运行入口，不重写采集器：

| 工具参数 | 上游执行方式 |
|---|---|
| `source`, `keyword`, `limit`, `deep` | `fetch_news.py --source ... --keyword ... --limit ... --deep --no-save` |
| `profile` | `daily_briefing.py --profile ... --no-save` |

两种调用方式择一。`limit` 是**每个来源的采集数量**，最终简报条数由用户要求决定。可用来源由原版 `--list-sources` 返回，预设使用原版分组、数量及深读配置。默认开启深读。

`news_collect` 只接受固定脚本及已知参数，不接受任意命令；保留工作台已有的工具授权和审批流程。运行前核对上游文件哈希。采集耗时上限 210 秒，过大范围可拆分或缩小来源。

原始 JSON、Markdown 报告保存在 `.panel-data/reports/YYYY-MM-DD/`，文件名含独立运行编号或随机编号。原始数据不经过澎湃域名过滤，报告也不套用旧的澎湃渲染器。旧的 `thepaper-daily-news` 独立技能仅供主动绑定它的 Agent 使用，Aria 不绑定它。

## 日期与可用性

遵循原版日期规则，包括 Smart Fill 补足标记和国际来源滚动 24 小时窗口；不沿用旧版固定当天、固定 1–5 条的强制过滤。提示 Aria 优先遵循用户指定日期，但筛选与事件合并由模型处理，不是代码核验保证。实测中模型仍可能选择滚动窗口内的前一天条目，应以显示的来源日期为准。上游部分来源仅提供 `Today`、`Hot`、`Real-time` 或未知时间，这些值不能证明原始报道今天发布。

工作台额外提供 `time_hints`，把有明确时区的来源时间换算成北京时间，便于 Aria 筛选和展示；原始数据与来源时间保持原样。未知时区不猜测，来源时间也不自动等同于事件发生时间或首次发布时间。

部分站点可能返回空数据、验证码、仅站名或标题。报告只能根据实际取得的数据汇总；缺正文时说明仅取得标题或摘要，必要时调用已授权 `read_web` 补读。采集日志中的 `deep_items` 仅指存在 content 字段的数量，不代表全部已取得有效正文；`short_content_items` 记录过短内容。

## 一般联网搜索

新闻技能之外，Aria 继续由模型按不确定性、实时性和用户要求选择联网工具。DeepSeek Search / Fetch 连接保留，用于 `web_search` / `read_web`，不再承担澎湃专用新闻适配。

能力连接选择 **DeepSeek · 官方 Search / Fetch**，地址 `https://api.deepseek.com`，使用自己的 API Key。密钥继续使用本机加密存储。其它 Agent 可以独立选择搜索服务与新闻技能。

## 安装与排查

```powershell
.\pywechat-main\.venv\Scripts\python.exe -m pip install -r requirements-panel.txt
.\pywechat-main\.venv\Scripts\python.exe -m playwright install chromium
.\pywechat-main\.venv\Scripts\python.exe -X utf8 skills/news-aggregator-skill/scripts/fetch_news.py --list-sources
```

运行详情应出现“原生新闻技能就绪”“原生新闻脚本执行”“原生新闻报告已保存”，可以查看脚本、来源/预设、条数、采集诊断和本地报告路径。失败时检查对应源的网络访问和 Python 依赖；HF Papers、Ben’s Bites 等浏览器来源还需要 Playwright Chromium。

## 上游审查记录

安装前使用 `skill-vetter` 审查 7 个执行文件：fetch_news、daily_briefing、rss_parser、fetch_user_feeds、fetch_hf_papers_playwright、fetch_bensbites、fetch_generic_playwright。其余导入内容为技能说明、模板与依赖清单，没有安装调试或测试脚本。

- 脚本访问公开新闻站点和 RSS，执行子进程使用当前 Python；浏览器为新建的无头会话，不读取个人浏览器登录状态。
- 自定义订阅会按上游约定读取 `~/.config/news-aggregator/user_sources.opml` 或技能目录内的 OPML；报告写入本机数据目录。
- 保留了上游 RSS 请求使用 `verify=False` 的行为，这是原版的 TLS 校验限制；文件哈希用于核对安装内容，不是网络数据真实性证明。
- 上游没有随本次快照提供 LICENSE，未擅自补写授权声明。

## 验证

`tests/test_news_aggregator.py` 覆盖原版文件一致性、CLI 参数、原始多来源 JSON 透传、预设分组、权限保留、审批恢复、报告保存、普通聊天隔离、未执行/空结果防误报。一般搜索与独立旧技能的回归由 `tests/test_deepseek_news.py`、`test_web_policy.py` 保留。

真实网络及 Aria 测试使用隔离测试台会话，不向真实微信联系人发送测试消息；记录保存在本机 `output/verification`。

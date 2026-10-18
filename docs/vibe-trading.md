# Vibe-Trading 微信 Agent

AI 中心 → 新建 Agent → Agent 来源选择 **Vibe-Trading 金融研究**，选择已有模型连接并保存。点击卡片上的“检查引擎”确认环境和实际可用工具，再点击“测试 Agent”。联系人可以绑定这个 Agent，沿用原有 AI 辅助（生成草稿）或 AI 自动回复流程。

## SparkFlow 同源模式（2026-10-17）

根据后续要求，当前微信金融助手改用 `vibe_engine: "sparkflow"`。从本机 SparkFlow 的 `services/vibe-trading` 逐文件复制源码、使用其 Python 3.11.9 与依赖锁文件；不修改 SparkFlow 的源码、配置或历史。独立快照位于 `.runtime/vibe-sparkflow/source`，清单 `.runtime/vibe-sparkflow.json` 保存原路径、各文件 SHA-256 和整体指纹。原 GitHub 最新版安装仍保留，以下旧版说明只适用于 `vibe_engine: "github"`。

同源模式直接使用原生 `ChatLLM`、`AgentLoop`、提示词和 `build_registry`，最多 50 轮。当前环境加载 76 项工具、87 项技能；不再限制为旧适配器的 14 项工具和 8 项技能。沿用 SparkFlow 服务端关闭 shell 工具的设置，用户历史和券商凭据不复制。工具注册成功仍不保证外部服务总是可达。

配置页面展示原生工具/技能清单及只读的 50 轮上限；保存值与实际执行一致。同源模式 `vibe_task_timeout=null`，与 SparkFlow `SessionService` 一样不额外设置整任务截止时间，仍可取消任务。历史内容直接调用原生 `SessionService._convert_messages_to_history`，按 12000 字符预算处理。入口及模块使用版本 URL 和 no-store 响应头；旧页面未提交引擎字段时保留已选同源引擎，不恢复旧提示词、12轮或1800秒限制。

2026-10-17 数据缺口复核：运行 `49de83bc-a8ba-458c-a3c1-44844f935b89` 已取得 DIA、IWM 的行情结果，之后发生多次 `tool_skipped`。同源源码中的去重按工具名判断，部分工具首次成功后不允许再次查询不同参数；每次返回给模型的工具结果另有 10000 字符上限。原 SparkFlow 也使用这些逻辑；其参考报告额外调用了网页搜索和正文读取。当前按用户要求保留原生实现，没有为消除“缺口”改写提示词、去重或数据结果。

同步脚本复制 SparkFlow 中选定的 DeepSeek 模型地址、名称和密钥到微信独立模型连接，密钥以 Windows DPAPI 加密保存。原生模型调用使用 temperature=0、超时180秒、重试2次，与 SparkFlow 一致；不注入 Relay 的 `max_tokens` 或 `thinking: disabled`。模型页面的通用输出上限不参与同源原生调用。

同时复制 SparkFlow 启动代码中显式设置的 HTTP/HTTPS/ALL_PROXY/NO_PROXY，以及原生 `~/.vibe-trading/memory` 偏好文件。当前原生偏好包含用户保存的“宏观研报模板与风格”，由原生记忆检索器读取；不是微信侧另写的要求。微信侧工作目录按 Agent 与联系人/测试会话隔离，后续记忆只写入微信自己的目录，不写回 SparkFlow。

“金融助手回复要求”已移除，保存 Vibe 配置会清空 `prompt`、`style_id`；执行时也忽略历史遗留的自定义回复提示词。同源模式不附加“300字”、微信措辞、数字说明或二次总结。超过1999字的回复按原文边界分段，所有分段拼接等于原文；任一段发送失败或不确定时停止后续段。自动回复和辅助草稿两种模式继续有效，草稿确认支持完整报告。单次回复目前仍有16000字的工作台存储限制，超出时报错、不截断。

微信文字气泡不渲染 Markdown 表格/标题，因此视觉布局与 SparkFlow 网页不同。相同源码和模型不保证逐字重复生成；网络资料、对话上下文和采样结果仍会变化。微信上下文由联系人/测试会话独立维护，不读取 SparkFlow 历史。

```powershell
.\pywechat-main\.venv\Scripts\python.exe scripts/install-sparkflow-vibe.py --sparkflow C:\Users\ATLAS\Documents\SparkFlow
.\pywechat-main\.venv\Scripts\python.exe scripts/sync-sparkflow-vibe.py --sparkflow C:\Users\ATLAS\Documents\SparkFlow --agent-id <已有金融Agent-ID>
.\pywechat-main\.venv\Scripts\python.exe -m pytest tests/test_vibe_sparkflow.py tests/test_vibe.py -q
.\pywechat-main\.venv\Scripts\python.exe scripts/verify-vibe-trading.py --live --agent-id <Agent-ID> --case recap
```

安装不会覆盖已有快照。配置同步会在 `.runtime/vibe-before-sparkflow-*.json` 保存旧 Agent 配置，并保留旧模型连接。测试问题为“昨天美股市场的情况怎么样？帮我汇总一下。”；报告保存到 `test-results/vibe-recap-verification.json` 和 `.md`。验证使用临时数据库，不向微信联系人发送消息。

## 按需 PDF 研报

明确说“以 PDF 格式发给我”“生成 PDF 报告”时，系统将完整正文排版为海军蓝＋香槟金的 PDF，自动回复模式发送文件；辅助模式先生成 PDF 草稿供预览、编辑后确认。只提到“分析这份 PDF”或明确“不用 PDF”不触发导出。普通问题继续原文字回复。

排版支持中文嵌入字体、分级标题、段落、引用、Markdown 表格、跨页重复表头、可点击来源及页码。样稿在 `output/pdf/financial-report-preview.pdf`，示例数值明确标为虚构。原研究结论保留在运行详情里；界面提供下载链接。只有 PDF 请求会告知原生 Agent 返回结构化正文，由本地排版器生成文件；不会给普通回复增加格式提示。

“把刚才的报告做成 PDF”会在同一联系人/Agent（测试时同一测试会话）中导出上一份报告，不重新生成分析；包含“更新、重新分析、最新”等要求时仍交给模型处理。“先不要发送”保留为草稿。

PDF 通过原有串行微信工作进程发送，暂存/恢复输入框草稿，核对新增本人文件卡片；兼容粘贴即发送和需要点击发送的版本。不确定时不自动重发。2026-10-17 向用户指定的 Aria 实发样稿，已读取到 `financial-report-preview.pdf` 的本人文件卡片。接口 `GET /api/ai/runs/{id}/pdf` 只读取对应任务生成的报告，不接受任意文件路径。

样式参考：[Goldman Sachs AM 2026 Outlook](https://am.gs.com/cms-assets/gsam-app/documents/insights/en/2025/Investment-Outlook-2026.pdf)、[Blackstone 2026 Investment Perspectives](https://www.blackstone.com/insights/article/office-of-the-cio-2026-investment-perspectives/)、[Morgan Stanley The BEAT 2026](https://www.morganstanley.com/im/publication/insights/flash-reports/flashreport_thebeatoutlook2026_en.pdf)。采用自有品牌和版式，不冒充机构出版物。

## GitHub 兼容模式的源码与安装（旧版）

本次适配直接获取 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)，2026-10-17 查询时的 GitHub `main` 最新提交为 [`596fcf3d63eb4d96e067f6dc94742b5f74a230ea`](https://github.com/HKUDS/Vibe-Trading/commit/596fcf3d63eb4d96e067f6dc94742b5f74a230ea)。SparkFlow 仅作为集成方式参考，运行时不使用其旧源码、模型配置、用户数据或 Python 环境。

```powershell
.\scripts\install-vibe-trading.ps1
```

需要已安装 `uv`。安装器下载固定提交的 GitHub 源码归档，校验 SHA-256，创建独立 Python 3.12 环境，并用上游 Windows 哈希锁文件安装依赖。重复安装会核对版本标记；发现未知版本时保留原目录并提示处理。固定提交保证经过测试的适配可复现，不会在启动时悄悄跟随 main 更新。

- 源码：`.runtime/vibe-trading/source`
- Python：`.runtime/vibe-trading/venv`
- 安装记录：`.runtime/vibe-trading.json`
- 归档 SHA-256：`71C9041E27FD137DA15F5BDBAF2449C4E481C44C051AB7785A98DAA513FF3212`
- 上游许可：MIT；保留在下载源码的 `LICENSE`，附副本于 `docs/licenses/Vibe-Trading-MIT.txt`。

## 实际接入方式

每次回复启动独立子进程，直接执行上游 `src.agent.loop.AgentLoop`、技能加载器、工具及数值证据校验。模型调用通过 JSON-lines 回传 Relay，复用已保存的 Chat Completions 兼容模型连接；模型密钥留在 Relay 进程。模型需支持工具调用。

工具结果、模型用量、实际技能列表及不可用工具均记入运行详情。联系人上下文以及测试会话分别隔离；取消任务和消息变化沿用已有失效检查，过期回复不会进入发送队列。关闭联网后仅保留计算工具。子进程使用独立 HOME，但这不是操作系统级沙箱。

上游期权计算返回的 `price` 字段会被其通用校验误判为市场报价。适配层只将该理论值重命名为 `theoretical_value`，仍保留原生标的身份与数字证据校验；没有关闭校验来放行数字。

## 技能和工具

提供 8 个可单独勾选的原生研究技能：研究纪律、基础技术分析、财务报表、估值模型、全球宏观、风险分析、ETF 分析、期权收益分析。技能是方法说明，实际操作仍由已选择的工具决定；技能中的示例数值不是实时资料。

| 工具 | 用途 |
| --- | --- |
| `search_symbol` | 核实证券代码、市场和标的身份 |
| `get_stock_profile` | 估值指标、财务数据、机构持仓及上市地资料 |
| `get_stock_news` | 公司新闻 |
| `get_fundamentals` | 基本面指标 |
| `get_financial_statements` | 财务报表 |
| `get_macro_series` | FRED 宏观序列，需要数据商密钥 |
| `get_market_data` | 历史行情 |
| `get_options_chain` | 期权链 |
| `options_pricing` | 理论期权价值和 Greeks |
| `options_payoff` | 到期收益计算 |
| `financial_rigor` | 财务精确计算、交叉核验 |
| `technical_indicators` | 技术指标 |
| `etf_holdings` | ETF 成分和权重 |
| `web_search` | 公开网络搜索 |

证券类工具必须同时启用 `search_symbol`，供上游先核对标的。当前安装未提供 FRED 密钥传入配置，因此 `get_macro_series` 会报告不可用，其余 13 项可注册（此外还有内部 `load_skill`）。注册成功不等于数据源永远可用；Yahoo、东方财富等接口仍可能限流、缺字段或网络失败。

这是原生引擎的金融研究接入。未接入上游券商交易、账户操作、任意终端/文件、回测沙箱和多 Agent 群组工具；未勾选的技能不能通过路径或名称绕过限制。当前支持文字消息及近期对话，不支持图片分析或陪伴模块的长期记忆功能。

## 验证

### 研究报告发送与排版

Vibe 完成回复后，由本地交付层判断是否属于实质金融分析。包含金融依据、推理和足够分析内容的回复默认生成 PDF；闲聊、简短报价、概念解释和单纯取数失败说明仍发文字。用户明确要求文字时优先发送文字；明确要求 PDF 时也可导出短回复。判断不修改 SparkFlow 原生提示词、模型参数、研究结果或工具返回。

自动回复模式下，开始分析时先发送一次进度提示。分析请求或实际分析工具调用可触发提示；普通行情查询不会单独触发。提示使用独立队列类型，不写入模型聊天上下文，不会提前完成研究任务，也不会因提示未确认而取消最终报告。新消息、暂停或人工接管会阻止过期提示发送。测试、辅助回复和要求先不发送的任务不自动发提示。

PDF 使用海军蓝、香槟金和统一中文字体，支持数字列对齐、跨页表头、章节衔接、中文标点避头及等宽字符图。标题元信息仅显示本地日期时间，无分隔横线。报告可从运行详情下载；发送使用文件消息，不截断成第一段文字。

相关回归：`tests/test_financial_pdf.py`、`tests/test_research_delivery.py`、`tests/test_wechat_files.py`。

```powershell
.\pywechat-main\.venv\Scripts\python.exe -m pytest tests/test_vibe.py -q
npm run test:ui -- --grep Vibe-Trading
.\pywechat-main\.venv\Scripts\python.exe scripts/verify-vibe-trading.py --live --provider-id <已有模型ID>
```

集成测试覆盖真实原生循环与计算工具、技能授权、禁用联网、联系人隔离、草稿/队列路由、取消、测试会话历史及任务时限。浏览器测试覆盖来源选择、工具技能保存、重新编辑、引擎检查和测试回复。

实网脚本复制所选模型配置到临时测试库，只发送合成测试问题，调用原生技能、期权计算和公开公司数据，不连接微信发送消息。结果与调用轨迹写入 `test-results/vibe-live-verification.json`。它会调用模型 API，按你的模型服务计费；测试报告只保留模型名、合成问题结果及工具轨迹，不包含密钥。

也可以用 `--live --agent-id <Agent ID>` 原样复核已保存 Agent 的工具、技能、模型和输出长度配置；此模式不提高模型输出上限。

新增 API：`GET /api/ai/vibe/catalog`、`POST /api/ai/agents/{id}/vibe-test`。Agent 配置使用 `kind: "vibe"`、`vibe_tools`、`vibe_skills`、`vibe_max_iterations` 和 `vibe_task_timeout`。

## 行情查询失败的排查

“检查引擎”验证的是安装和工具能否加载，不会逐个请求行情数据源。可加载与数据源当前可连接是两种状态。

2026-10-17 排查发现：上游 `search_symbol` 用 CCXT 请求币安/OKX 的公开标的目录，而 CCXT 默认关闭 Requests 的 `trust_env`。Windows 只配置系统代理、没有 `HTTP_PROXY` 等环境变量时，会出现“普通行情请求可以访问，代码核验请求却连接失败”。适配器现在只在 Vibe 子进程中启用 CCXT 的 `requests_trust_env`，复用现有系统/环境代理，未修改系统代理配置，也未关闭标的身份校验。

`invalidated` 表示标的核验未完成，可能由连接失败引起，不是要求额外的交易账户授权。宽泛搜索 `bitcoin` 可能返回同名公司和 ETF，应查询完整交易对；`BTC-USDT` 和 `BTC-USD` 计价币种不同，切换时必须重新核验并说明来源。

可运行 `scripts/verify-vibe-trading.py --live --agent-id <Agent ID> --case crypto`，使用“比特币现在的情况怎么样？可以买吗？”验证完整链路。脚本必须取得真实 BTC 行情数据才算通过，结果存入 `test-results/vibe-crypto-verification.json`，不发送微信消息。

回复中的“略去无法与本会话工具数据对上的数值”来自原生数值证据校验；它不等于行情接口失败。应查看运行详情中是否已有成功的工具结果，不应关闭校验来展示未核实的数字。

若日志提示“模型输出被长度限制截断”，数据可能已取得，只是回复生成失败。原生引擎还需要生成工具调用和内部数字来源声明，1000 tokens 的输出上限容易不足；建议为 Vibe 建立独立模型连接，将输出上限设为 4096。聊天正文仍按简短回复要求生成。询问“现在”时优先取最近小时/分钟行情，并明确最后数据时间，不能用旧日线冒充实时价格。

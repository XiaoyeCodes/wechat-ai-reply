# 陪伴 Agent 使用与外部服务接入

## 使用阿慧

打开 AI 中心，在 Agent 卡片点击「测试 Agent」。同一浏览器中，所选 Agent 的测试会话连续保留近期对话及独立记忆。测试台不会向微信发消息。测试数据不混入任何真实联系人。

「记忆与档案」可选择 Agent 和用户（或测试台会话），设置称呼、城市、生日、关系起始日期、亲密度、年龄自我声明、长期记忆和关怀授权。无需身份证照片。默认未确认年龄时使用普通陪伴；未成年人始终不使用亲密关系设定。日常回复不反复声明身份，认真问到时说明是 AI。

本机记忆含近期窗口、滚动摘要、结构化事实，支持编辑、固定保留、自动过期及遗忘。只有用户明确提到且可在原文中找到证据的事实才自动保存。删除时清空摘要和旧上下文，防止从旧消息重新提取。普通本机事实默认保留 180 天；外部服务的物理保留期由外部服务控制，工作台检索只使用仍存在的本机事实。远端清理失败会明确报错，请重试后核对服务商后台。

API Key、记忆、媒体、运行上下文及运行追踪使用当前 Windows 用户的 DPAPI 加密。原有微信 messages 表仍是本机明文聊天记录。外部模型处理所需聊天内容会发送到你配置的模型服务；外部记忆、图片、语音及观测分别由自己的开关控制。观测默认只传运行指标，正文需额外勾选。

## 能力连接

| 能力 | 直接支持的协议 | 需要的配置 |
|---|---|---|
| 联网 | Agent Reach / Exa MCP、Exa API、Tavily API | 本机 mcporter 的 exa 通道，或服务 API Key |
| 天气 | Open-Meteo | 城市名，无需 API Key |
| 向量记忆 | Qdrant REST | 地址、集合、可用的 Embedding 模型连接 |
| 记忆框架 | mem0 REST | API 根地址、API Key |
| 其他记忆服务 | HTTP 记忆网关 | pgvector、Chroma、Letta、Zep 服务的适配网关，协议见下文 |
| 语音 | ElevenLabs、MiniMax、OpenAI Speech 兼容、HTTP 网关 | 语音 Key、实际可用的模型及音色 ID |
| 图片理解 | Chat Completions 多模态模型 | 独立模型连接，需要支持 image_url |
| 图片生成 | Images 兼容接口、HTTP 图像网关 | 图像模型；参考图 / LoRA 使用网关协议 |
| 可观测性 | Langfuse、LangSmith、HTTP 网关 | 服务地址与密钥，Langfuse 另需 Public Key |

未配置服务时不会伪造天气、搜索结果、声音或图片。能力连接的「测试能力」会真正请求接口，语音 / 生图测试可能计费。测试结果可预览下载。CosyVoice、火山、Flux、SD 的部署协议各不相同，选择兼容接口或按下述协议写薄适配层；填写厂商名称本身不会自动部署模型。

生成音频目前在面板播放和下载，未模拟微信原生语音气泡；生成图片也在面板预览下载。图片理解支持测试台上传，以及微信新收到图片的原图复制。取图时会短暂还原对应聊天窗口，通过图片菜单复制，核对剪贴板确实更新，再恢复原剪贴板及托管状态。不会把未加载的占位图交给模型；无法获取时在消息下说明原因。已在微信 4.1.15.53 验证。语音转写及微信原生媒体发送仍需专用客户端适配。

图片理解需要你自行配置支持 image_url 的多模态模型；是否支持以实际模型能力和连接测试为准。模型设置中可选择 DeepSeek 推理模式，即时聊天默认关闭推理，避免推理内容耗尽较小的回复额度。记忆抽取及图片描述使用简短的非推理请求。

## 表达节奏与系统提示词

Agent 编辑器的「系统提示词」与风格卡、人设、动态记忆一起组成模型的 system 消息。「记忆与档案 → 预览分层提示词」显示首个模型步骤的完整合成提示词示例；时间、记忆、工具结果和表达倾向在每轮更新。

「人设与表达」支持自然回复节奏与 Emoji 使用倾向（0～1，默认 0.25）。同一轮的表达选择在工具调用、审批和重试时保持一致；最近两条回复使用过 emoji，或遇到严肃情绪时，优先不用。该数值控制“允许使用”的倾向，模型仍可选择不用，不是强制给固定比例的消息添加表情。关闭自然节奏后，完全遵循自定义说话方式。

来源展示默认智能模式：短事实问答不加参考尾巴；长外部材料被明显压缩为摘要，或用户索要出处时附真实来源。可以改为仅应要求附带或每次附带，用户当轮明确不要链接时优先遵从。所有实际查询的来源仍保留在运行详情，方便核对。

## 按需知识与 Skill

Agent 编辑器中的「知识检索方式」支持按需、每轮和不检索。默认按需：只有绑定了知识库且模型确实需要资料时，才调用 `search_knowledge`。空知识库和日常闲聊不会产生空的“知识检索”步骤。个人记忆单独按会话读取，不依赖知识库。

在「AI 中心 → Skill 技能 → 新建」导入 `SKILL.md` 或技能文件夹，预览后保存，再在 Agent 编辑器勾选绑定。支持 YAML 的 `name`、`description` 和 Markdown 正文；参考文档按相对路径保存。运行时先通过 `use_skill` 加载步骤，需要附件时调用 `read_skill_file`，然后调用这个 Agent 已授权的工具。运行详情显示加载、附件读取和工具结果。

示例包见 [`examples/skills/web-video-reader/`](../examples/skills/web-video-reader/)，需要手动导入并绑定 Agent。技能通过 `read_web` 读取网页和 B 站字幕。脚本源码可以导入查阅，但本版不直接执行任意 shell 脚本；需要执行自编程序时，把程序接成现有 HTTP 工具并授权给 Agent。导入技能不会自动安装其依赖或获得额外权限。

普通网页使用 Jina，失败时读取公开 HTML 正文。B 站使用 bili-cli 元数据及 OpenCLI 字幕读取；浏览器桥的 adapter 导航不可用时，使用独立命名的 browser 会话调用同一只读字幕适配器。需要浏览器桥保持连接，并具备视频所需的登录状态。仅取得标题时会明确说明，不能把标题冒充字幕或视频全文。查询失败作为工具结果交给模型解释，不中断整轮文字回复。

## 服务地址

- ElevenLabs：`https://api.elevenlabs.io`，请求 `/v1/text-to-speech/{voice_id}`。
- MiniMax：`https://api.minimax.io`，请求 `/v1/t2a_v2`，固定 `output_format=hex`。
- OpenAI Speech 兼容：填写 API Base URL，请求 `/audio/speech`。
- Images 兼容：填写 API Base URL，请求 `/images/generations`。仅文字生图；参考图 / LoRA 不能通过这个选项传递。
- mem0：填写 API 根地址，搜索 `/v2/memories/search/`，写入 `/v1/memories/`。
- Langfuse：根地址，请求 `/api/public/ingestion`；LangSmith：API 根地址，请求 `/runs`。
- HTTP 网关：填写完整 POST 地址。认证为 `Authorization: Bearer <API Key>`，空 Key 可用于本机服务。

## HTTP 记忆网关

所有操作均须按 `namespace` 隔离，namespace 是本机对 Agent、微信账号及联系人计算出的不透明标识。测试会话有独立标识。不同 namespace 之间不可检索、覆盖或删除。

```json
{"operation":"upsert","namespace":"...","id":"本机记忆ID","content":"用户喜欢清淡的食物"}
```

更新相同 namespace / id，而非重复新增。可在网关中实现 pgvector、Chroma、Letta 或 Zep 的实际读写及抽取逻辑。工作台的本机事实是最终依据，外部搜索只决定排序。

```json
{"operation":"search","namespace":"...","query":"晚餐吃什么","limit":6}
```

响应：

```json
{"memories":[{"namespace":"...","local_id":"本机记忆ID","score":0.92}]}
```

工作台使用仍然存在、未过期的本机记忆内容，拒绝远端返回的其他用户标识或已删除 ID。`clear` 操作删除此 namespace 全部记录；`delete` 操作带 id，删除单条记录。错误请返回非 2xx，避免冒充删除成功。mem0 单条遗忘通过清空当前 namespace 后重建保留的本机记录完成。

Qdrant 的连接测试读取 Embedding 维度，并在不存在时创建指定集合；请使用独立集合。Embedding 模型必须与已存向量维度一致，DeepSeek 文本聊天连接不等于 Embedding 服务。

## 语音和图像网关

语音请求：

```json
{"model":"your-model","input":"今天辛苦啦。","voice":"voice-id","speed":1,"response_format":"mp3"}
```

返回 `audio/mpeg` / `audio/wav` / `audio/flac` 二进制，或 `{"audio_base64":"...","mime":"audio/mpeg"}`。使用获得授权的固定或克隆音色；音色克隆的录音上传、训练和授权由服务商管理。

图像请求：

```json
{"model":"flux-or-sd","prompt":"...","n":1,"reference_image":"图片base64或null","lora":"ahui","lora_scale":0.8}
```

返回 `{"image_base64":"..."}` 或 `{"url":"公开图片下载地址"}`。网关负责把这些参数转换为部署平台实际的参考图、LoRA、ControlNet 等接口。工作台不会把参考图 / LoRA 当作保证相貌一致的承诺。

## 主动关怀

需要三处同时开启：Agent 允许主动关怀、用户档案同意主动关怀、日程启用。联系人还须开启监听，绑定对应 Agent 并处于 AI 模式。默认生成草稿；选择自动后仍遵守 AI 辅助/自动会话模式。

支持一次性时间和五段 Cron。时间使用 IANA 时区，默认 Asia/Shanghai；每天 09:00 是 `0 9 * * *`，每天 22:00 是 `0 22 * * *`。数据库先登记本次执行，重启不重复；过期超过 5 分钟、免打扰、全局暂停、无授权、超过每日次数或正在回复用户时跳过，并保留原因。

调度器随本机服务运行，不是 Windows 系统任务。电脑关机或服务关闭期间无法推送，也不会启动后补发积压的问候。

## 外部 Agent

原有 HTTP Agent 除 `message / conversation / contact / run_id / dry_run` 外，还可收到 `system_prompt` 和 `companion_context`。没有选择文本模型时，本机不执行 LLM 记忆抽取，但仍可提供现有记忆及基础安全检查。外部服务必须自行遵守测试标志和其内部工具权限。

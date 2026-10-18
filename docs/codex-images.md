# Codex / ChatGPT 账号生图

通过本机 Codex CLI 的内置生图工具，复用已登录的 ChatGPT 账号。支持文生图和单张参考图编辑；不读取或复制登录令牌，不需要 OpenAI API Key，不自动切换付费 API。

## 启用

1. 安装 Codex，在运行 Relay 的同一 Windows 用户下执行 `codex login`，选择 ChatGPT 登录；`codex login status` 应显示 `Logged in using ChatGPT`。
2. 启动或重启 Relay。扩展中心 → 连接器会出现 **Codex / ChatGPT 账号生图**；技能中会出现 **codex-image-generation**。
3. 编辑需要使用的本地流程 Agent，在「图像生成」卡片点击「启用能力」。这会绑定生图工具与技能；在声音与视觉中选择 **Codex / ChatGPT 账号生图** 并保存。其他 Agent 和联系人的授权不会自动改变。
4. 测试台发送「画一只在窗边看雨的橘猫」进行文生图。图生图则上传照片并发送「把背景换成海边，保留人物与服装」。图生图不强制要求额外配置图片理解模型。
5. 生图工具默认需要确认，可在工具权限中自行调整为自动执行。在微信自动回复模式下，生成成功后图片直接发送到原会话，不需要去面板下载。辅助模式先保留草稿，确认后发送图片；测试台仅预览、下载，不发送微信。

文生图和图生图共用图片发送队列，支持同一任务的多张生成结果；语音偏好不会把图片改成文字或语音。只发送本次任务生成的媒体，不发送参考图或其他联系人的图片。发送前再次核对会话与任务状态；新消息、暂停、人工接管会阻止过期发送。只有观察到新增的本人图片气泡才标记已发送，历史气泡、文件气泡不算成功。结果不确定时保留待核对状态，不自动重发；重启也不会重放旧队列。

连接器的「测试能力」会真实生成一张红苹果图片，消耗当前账号额度。生成通常需要数分钟，默认超时 300 秒，可配置为 5～900 秒。账号是否支持、配额是否可用，以真实调用为准。

## 工具参数

```json
{"prompt":"一只在窗边看雨的橘猫，暖色水彩风格","mode":"generate"}
```

```json
{"prompt":"把背景改为浅蓝色，保留主体和构图","mode":"edit"}
```

编辑模式优先使用本轮图片，否则使用当前联系人的近期图片；存在多张本轮图片时需指定 `reference_media_id`。该 ID 必须属于当前会话或当前联系人的微信图片。也可以修改工具刚刚生成并返回的图片 ID。缺少图片时返回错误，不会退回文字生图。固定角色参考图仍支持在 Agent 设置中指定；LoRA 仅支持原有 HTTP 图像网关。

图片和参考图按已有媒体策略在 Relay 本机加密保存，最多 8 MB、2000 万像素；面板副本最大边长 1536，保留透明通道。Codex 的原始生成文件保留在其 `generated_images/<thread-id>/` 目录。生成服务会将提示词和参考图提交给 Codex 使用的服务。

## 执行与故障

对于「帮我生成一张富春山居图」「画一只猫」等明确请求，普通本地流程会按当前工具授权直接调用生图，避免旧聊天中「没有画图功能」的回复覆盖新配置。新图不使用无关历史图片。图生图仍传入当前会话原图；既有确认策略、取消检查与失败不重试规则保持生效。询问教程、编写提示词、否定生图等消息不会触发这条直接调用路径。模型上下文也会提示以当前工具配置判断能力。

每次生成创建独立的临时工作目录与 Codex 临时会话，禁用 Shell、浏览器、电脑操作、子代理、插件、Hooks 和应用连接，使用只读沙箱。用户配置不带入该会话，Codex 自己管理登录状态。通过标准输入传递描述和独立图片附件，避免 Shell 插值。

只有当前任务编号目录下新生成且通过图片解码验证的文件才算成功，不相信模型自行编造的路径。超时、进程失败或任务失败标记为结果待核对，不自动重试；请先检查 Codex 生成目录，以免重复消耗额度。取消 Relay 任务后停止等待，并终止本机生图进程；已经提交的远端生成不保证撤销。

若提示未登录，请重新登录 Codex；若提示没有真实图片，请检查账号生图权限或额度。桌面端能生图不等于任意 CLI 版本都可调用；请使用支持 `exec --ignore-user-config`、`--ephemeral` 和 `image_generation` 的 Codex。

## 开发验证

```powershell
.\pywechat-main\.venv\Scripts\python.exe -m pytest tests\test_codex_images.py -q
npx playwright test tests/codex-images.spec.js

# 以下命令真实生成图片，消耗已登录账号额度。
.\pywechat-main\.venv\Scripts\python.exe scripts\verify-codex-images.py --output output\codex-image-test.png --prompt "白色背景上的红苹果"
.\pywechat-main\.venv\Scripts\python.exe scripts\verify-codex-images.py --reference output\codex-image-test.png --output output\codex-image-edit.png --prompt "保留苹果，把背景改成浅蓝色"
```

官方参考：[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)、[命令行参数](https://learn.chatgpt.com/docs/cli/reference)。这里调用 Codex 内置生图能力，而不是 Images API；本机能力须以实测确认。

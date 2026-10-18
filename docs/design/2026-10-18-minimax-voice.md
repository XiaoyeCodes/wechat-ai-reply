# MiniMax 微信语音接入

目标：聊天侧栏可配置 MiniMax Key、上传样音克隆、选择音色、切换 Speech 2.8 Turbo/HD，并用相同参数试听和自动回复。用户目前没有 Key 和样音，本次完成入口及模拟接口验收，不声明真实 MiniMax 合成已验证。

- 本机 Key 使用现有 Windows DPAPI 加密，只返回配置状态；固定官方中国站 API，不接受任意转发地址。
- 原音色继续可用，MiniMax 预设与克隆音色统一进入音色列表。选择及模型按联系人保存，修改不清空 Agent 记忆。
- 独立弹窗管理密钥、连接检测、上传样音。样音只在提交克隆时上传 MiniMax，限制文件大小、时长和音频格式，服务端验证后上传；不保存原始样音。
- 克隆请求使用持久化唯一请求 ID，超时不自动重新创建。克隆成功不自动绑定联系人、不自动激活付费音色。首次正式试听或回复会激活音色，界面明确提示费用。
- 试听与自动回复使用同一合成入口。API 异常返回脱敏原因；开始录音前失败可降级文字，开始录音后不补发。音色或模型在生成/录制期间切换，取消旧语音。
- 验证：MockTransport 覆盖 HTTP 合成、上传/克隆、计费请求不重试、认证与配额异常、音频验证和密钥不外泄；Playwright 验证配置、克隆、试听、持久化和按联系人隔离；真实面板无凭证状态截图。

官方参考：
- https://platform.minimax.cn/docs/api-reference/speech-t2a-http
- https://platform.minimax.cn/docs/api-reference/file-management-upload
- https://platform.minimax.cn/docs/api-reference/voice-cloning-clone
- https://platform.minimax.cn/docs/api-reference/voice-management-get
- https://platform.minimax.cn/docs/pricing/overview

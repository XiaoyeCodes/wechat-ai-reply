# 后端实现交付

2026-10-19，基于 `.product-team/brief.md` 与 `.product-team/architecture.md`。

## 已实现

- 新增 `wechat_panel/agent_schedules.py`，独立的公共 Agent 定时任务 CRUD、一次性/五段 cron/IANA 时区、启停、手动立即执行、符合条件人数与执行历史接口。
- 一个匿名生成运行、每位收件人一个无模型的投递运行；文字分片与图片共享既有串行队列及气泡核对，不逐人调用模型。
- 开始候选快照、生成结束交集、联系人独立 `ai_delivery_revision`、每次任务 1 小时有效期；发送前再次检查监听/自动回复/Agent/开关/任务 revision。
- 独立 occurrence scope，不读取任何联系人档案、记忆或聊天；保留 Agent 静态规则并明确本轮是公共任务，覆盖私人称呼与亲密互动。过滤个人记忆工具，跳过伴侣情绪分析和新闻/生图快捷路由。
- 保留当前工具确认与运行详情。禁止以 dry_run 模拟真实工具执行。当前公共任务仅接受 workflow Agent；其他 Agent 类型明确报错。
- SQLite occurrence 唯一认领、recipient 唯一认领、消息幂等键；重复立即执行返回 409。停用/编辑/删除取消旧执行和待审批。重启取消旧执行、不补发旧队列，发送不确定保留 uncertain。
- 原有联系人关怀日程保持独立，普通新消息不会取消已生成的公共推送；人工接管、关闭监听/自动回复、Agent 变更、暂停仍使旧投递失效。

## 文件范围

新增 `wechat_panel/agent_schedules.py`；修改 `wechat_panel/ai.py`、`ai_store.py`、`store.py`、`app.py`、`image_requests.py`、`web_policy.py`、`voice_reply.py`。未修改前端、测试、密钥配置或真实任务数据。

## 验证

- `pywechat-main/.venv/Scripts/python.exe -m compileall -q wechat_panel` 通过。
- `pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_ai.py tests/test_image_requests.py tests/test_image_delivery.py tests/test_voice_reply.py tests/test_companion.py -q`：88 passed，只有既有 Starlette httpx 弃用警告。
- 独立测试 Agent 提供的新增 45 项测试分批通过，最后完整总回归由协调与测试 Agent 负责记录。
- 未向真实微信发送任何测试消息；模型、数据接口、生图及图片发送的行为验证使用可替换适配器。

## 运行方式与限制

仍使用既有 `start-panel.ps1`。功能生效需重启工作台后刷新；由总协调管理现有 8766 服务，后端开发未自行重启真实运行进程。

当前版本没有内建后台服务常驻机制，应用关闭、电脑休眠或微信离线会影响执行；旧任务最多容许 5 分钟调度延迟，超过直接跳过。耗时生成或待审批占用 1 小时有效期，超时取消。发送按钮点击后无法撤回已发消息，准确结果以原微信核对状态为准。

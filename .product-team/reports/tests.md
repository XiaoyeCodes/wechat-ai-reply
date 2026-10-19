# Agent 定时任务独立测试

## 环境和边界

- Windows / Python 使用 `pywechat-main/.venv/Scripts/python.exe`，pytest 9.1.1。
- 所有新增测试使用 pytest 的临时目录和 SQLite；模型输出、图片字节与微信桥接均为本地模拟，不调用真实微信、模型 API 或 Codex 生图。
- 测试入口预定 `tests/test_agent_schedules.py`。产品代码由 backend 修改，测试工程师仅修改 tests 和本报告。

## 用例计划

| 验收条款 | 独立证据目标 |
| --- | --- |
| AC-01a/b | API CRUD、默认禁用、合法 IANA 时区、一次性/cron 下次时间；非法时区/时间/cron、空字段/不存在 Agent 4xx |
| AC-02a | 两名收件人只执行一回 fake model，生成 contact_id=null，不同 delivery run 复用相同正文 |
| AC-02b | 两联系人分别设置私人消息与长期记忆标识；公共生成的模型输入与状态不得包含标识 |
| AC-02c | confirm 工具执行前 pending approval；批准后一次执行，拒绝后不生成投递 |
| AC-03a | 同 Agent+listening+ai_auto 三项同时满足才投递，其他模式和绑定逐项排除 |
| AC-03b | 生成/排队后切换模式、关闭监听、切 Agent、暂停、关闭 AI、停用任务/Agent 后 ai_send_valid=false |
| AC-03c/04b | 甲回执失败不把乙写为失败；一份送达后仍准确维护乙的排队/不确定状态 |
| AC-04a | 长文完整且顺序拆分；共享图字节相同，现有 image_delivery.deliver 对 fake bridge 可验证 |
| AC-05a | 相同到期 stamp 连续及并发 tick occurrence 只认领一次 |
| AC-05b/c | 过期 occurrence 被跳过、cron 继续到未来；重启不重放 running/queued/uncertain，旧内容发送截止时间验证 |
| AC-06a/b | 无收件人不运行 fake model；暂停、停用 Agent/任务、配置异常可解释且无发送 |
| AC-07a | 执行原关怀关键回归，确认未扩大关怀/定时收件范围 |

## 实际结果

实现已落地，后端声明冻结后执行最终回归。新增测试文件当前包含 46 项（含参数化展开）。全部为模拟模型、模拟 PNG 和临时数据库；图片投递调用真实 `image_delivery.deliver`，底层 bridge 仅收集字节并返回假回执。

已完成的新增分批证据：

- `pytest tests/test_agent_schedules.py -q`：42 passed，1 warning，76.03s（当时收集的完整集合）。
- `pytest tests/test_agent_schedules.py -q -k restart_stops_unfinished_generation`：2 passed，42 deselected，4.68s。
- `pytest tests/test_agent_schedules.py -q -k immediate_double_click`：1 passed，44 deselected，2.17s。
- 后续在同一测试文件补充新闻源失败不广播，并增强静态 Agent prompt 保留断言，纳入最终回归。

最终命令（后端冻结后的完整独立回归）：

```powershell
& ./pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_agent_schedules.py tests/test_companion.py tests/test_agent_context_isolation.py tests/test_image_delivery.py tests/test_image_requests.py tests/test_ai.py tests/test_ai_api.py tests/test_store.py tests/test_runtime.py tests/test_conversation_turns.py tests/test_web_policy.py -q
```

实际结果：**180 passed，1 warning，201.99s（3分21秒）**，进程退出码 0。其中新增定时测试 46 项；原关怀、AI对话/API、图片、Agent/联系人上下文隔离、store/runtime、连续消息和web policy回归均通过。唯一 warning 仍为既有 FastAPI/Starlette TestClient 的 httpx 弃用提示。

覆盖证据对应：

| 条款 | 自动测试证据 |
| --- | --- |
| AC-01 | API CRUD 默认停用、部分字段编辑、11组非法配置、无panel标识403、时区UTC换算和DST不存在的本地时间 |
| AC-02 | 一次假模型生成复用两人；两人私人聊天/档案/记忆标识不进入公共输入；保留静态Agent指导；确认型工具批准/拒绝 |
| AC-03 | 5类不合格联系人排除；10项当前配置守卫；开始候选与完成资格交集；普通聊天不取消广播，关闭监听后重新开启也不能恢复旧投递 |
| AC-04 | 长文全量拆分且逐人送达/不确定回执独立；原daily-news技能mock模型先use_skill、再read_web、再一次generate_image；两个delivery读取相同scope中的相同PNG |
| AC-05 | 并发到期认领去重、立即运行双击去重、一次性/cron过期跳过、内容期限、发送中/排队及running/waiting_approval重启不重放 |
| AC-06 | 禁用/关闭AI/无收件人立即运行可解释；到期无收件人/暂停/关闭AI/停用Agent不执行模型；新闻源失败时不广播错误或未核实内容 |
| AC-07 | 既有AI对话、原关怀日程、图片快捷路由、记忆隔离、store/runtime/conversation/web policy回归 |

最终回归无失败项。前期两项失败均为测试数据缺少既有Store必填nickname，已修复并重跑通过。

既有行为基线已运行：

```powershell
& ./pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_companion.py tests/test_agent_context_isolation.py tests/test_image_delivery.py -q
```

实际结果：**26 passed，1 warning，28.76s**。warning 为既有 FastAPI/Starlette TestClient 的 httpx 弃用提示。该命令验证原联系人关怀、Agent/联系人记忆隔离和模拟图片串行发送，不代表新增定时功能已通过。

## 其他角色证据（转述，非本角色亲自运行）

总协调报告 2 项 Playwright 端到端通过：CRUD/每日周规则/启停/历史/手机宽屏；两位demo联系人点击立即运行→模型一次→双方模拟微信发送核对。后端报告 88 项旧AI/图片/语音/关怀回归通过。以上统计不与本报告独立Python结果相加宣称唯一用例总数。

## 明确未运行

真实微信投递、真实模型质量与付费生图、生成新闻图片视觉效果不在本轮自动测试范围。

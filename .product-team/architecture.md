# Agent 公共定时任务架构

2026-10-19。复用 FastAPI、SQLite、AIService 工具审批以及 Runtime 串行微信发送，不增加运行依赖。现有联系人主动关怀日程保留原路径。

## 数据流与隔离

一个 occurrence 只创建一个 `mode=agent_task`、`contact_id=null`、`dry_run=0` 的生成运行。独立 scope 为 `agent-task:<occurrence_id>`，不准备或读取任何联系人的陪伴档案、记忆、历史、情绪分析；保留 Agent 静态系统规则，但在本轮约束中覆盖个人称呼、亲密互动与个人背景。只接受 workflow Agent，Hermes 的专属联系人授权与 Vibe 的上下文隔离没有在本版扩大。

候选名单在生成开始时保存联系人 ID 与 `ai_delivery_revision`，生成后取仍符合条件的交集。仅符合 `listening=true && mode=ai_auto && ai_agent_id匹配` 的联系人可投递。普通新来信改变 `ai_revision`，不影响公共任务；监听、模式、Agent 变化改变独立的 `ai_delivery_revision`，旧结果永久失效。

生成完成后为每位收件人创建不调用模型的 `mode=agent_delivery` 子运行。子运行共享同一文字、同一 artifacts 和生成 scope。长文本使用 `enqueue_ai_reply` 原有分片，图片使用 `enqueue_ai_images`；Runtime、图片归属验证、发送前 guard 和气泡核对保持有效。公共任务文字不额外对不同收件人生成语音。

## 持久化与恢复

- `agent_schedules` 保存加密配置、revision、next_at、last_status。
- `agent_schedule_occurrences` 保存唯一 `(schedule_id, scheduled_at)`、source、生成 run_id、状态、加密 targets 快照。
- `agent_schedule_recipients` 唯一 `(occurrence_id,contact_id)`，记录独立投递 run_id 与状态。
- occurrence 在模型调用前 SQLite 事务认领。投递子运行与收件人 claim 同事务创建，消息使用原幂等键。
- 创建/编辑/停用/删除、AI 总开关、暂停、联系人权限变更均能令发送 guard 失效；待审批保留原审批流程且可取消。
- 定时触发超过 5 分钟跳过，并直接求未来下次时间，不逐个补发。每次任务从启动开始只有 1 小时有效期，等待审批/生图与发送排队均受截止时间约束。
- 重启不恢复旧生成、待审批或 fanout；已有发送意图保留 uncertain，不将未确认状态写成成功。不重建旧队列。

## API 合同

`/api/ai/agent-schedules` GET 返回任务数组，POST 创建；`/{id}` PUT 合并编辑、DELETE 删除；`/{id}/run` POST 返回新 occurrence（202），需先启用任务且符合运行条件；已有活动运行返回 409。`/{id}/runs` GET 返回最近 50 次历史。`/eligible?agent_id=...` 返回 `{count,contacts:[{id,lookup}]}`。

配置字段 `agent_id,title,instruction,kind(once|cron),at,cron,timezone,enabled`。时区默认 Asia/Shanghai，cron 为标准五段，一次性无时区时间按所选时区解释。任务响应增加 `id,next_at,last_status,eligible_count,created_at,updated_at`。

历史字段 `id,schedule_id,scheduled_at,source,run_id,status,error,created_at,updated_at,reply,text,media,recipients,deliveries,started_at,finished_at`。`text` 是 `reply` 别名；`deliveries` 是 `recipients` 别名，含 `contact_id,lookup,run_id,status,error,message_ids`。生成运行继续使用现有 `/api/ai/runs/{run_id}` 详情与审批接口。

## 风险与验证边界

发送按钮点击后的停用无法撤回已经发送的微信；会在耗时连接后和实际发送前重验，结果仍以原核对机制为准。生成模型与 Codex 图片服务受既有可用性和工具授权约束，不能用 dry_run 绕过权限。测试使用临时 SQLite 和模型/发送适配器，不向真实联系人发送。吞吐继续受现有两个 AI worker 和一个串行微信 worker 限制。

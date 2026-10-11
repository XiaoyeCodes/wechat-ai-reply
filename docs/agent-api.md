# 外部 Agent 与事件接口

### 接入你开发好的 Agent

在 Agent 来源中选择 **我开发的外部 Agent（HTTP）**，填写地址和可选 API Key，然后为对应联系人选择它。例如 客户甲选择电商 Agent，客户乙选择财务 Agent。外部 Agent 无需再绑定本地模型。

面板 POST 以下 JSON，密钥通过 `Authorization: Bearer <Key>` 发送：

```json
{
  "run_id": "本轮唯一 ID",
  "message": "本轮新消息",
  "conversation": [
    {"role": "user", "content": "上一条问题"},
    {"role": "assistant", "content": "已在微信核对的回复"},
    {"role": "user", "content": "本轮新消息"}
  ],
  "contact": {
    "id": "本地联系人 ID",
    "lookup": "联系人备注",
    "wechat_id": "联系人微信号",
    "account_id": "当前微信账号"
  },
  "dry_run": false
}
```

返回 `{"reply":"要回复的正文"}`。测试台的 `dry_run` 为 true、contact 为 null。外部服务应按 run_id 做幂等处理，并自行管理内部工具权限、任务确认及 dry_run 行为；本地面板的工具确认仅管辖本地流程的工具。所有微信回复仍经过联系人绑定、模式与发送队列检查。

## Agent 入站事件接口

设置服务环境变量 `WECHAT_PANEL_AGENT_TOKEN` 后，可读取经核验的新入站文本事件：

```http
GET /api/agent/events?after=0
Authorization: Bearer <配置的令牌>
```

```json
{
  "events": [{
    "event_id": 42,
    "account_id": "当前微信账号",
    "contact_id": "本地联系人 ID",
    "wechat_id": "联系人微信号",
    "message_id": 18,
    "text": "用户消息",
    "received_at": "ISO 时间"
  }],
  "next_cursor": 42
}
```

客户端保存 `next_cursor`，后续从该游标继续读取。历史同步、系统消息、方向未知的消息及本人消息不产生 Agent 事件。读取接口不会消费或删除事件。令牌未配置时接口返回 503。

该只读接口本身不授予发送消息或执行任务的权限。AI 中心的外部 Agent 接入会主动 POST 消息，无需同时轮询此接口。接口结构也可从 `/api/agent/schema` 查看。

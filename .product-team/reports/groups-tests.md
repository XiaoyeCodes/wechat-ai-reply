# G1 群监听独立测试报告

## 环境与边界

Windows，Python使用 `pywechat-main/.venv/Scripts/python.exe`，pytest 9.1.1。新增 `tests/test_group_chats.py` 当前79项（参数化展开）。数据位于pytest临时目录；模型、UIA树、微信bridge均为假实现或DemoBridge。串行worker测试明确传入demo=True。

没有操作真实群、没有真实微信发信、没有使用付费模型/生图。没有真实群名，真实微信版本的群消息UIA可见性与发送效果仍未实测。

## 对应验收条款

| 条款 | 测试内容 |
| --- | --- |
| G1-01 | API省略微信号创建群、group inspect操作、私聊仍必须微信号、群类型/完整名称/账号/内部key与成员核验 |
| G1-02/03 | 已核验指定成员@当前群昵称触发；9类不合格元数据不触发；可见完整@边界、@他人、邮箱、昵称前缀、伪sender正文、UI引用及行内引号 |
| G1-04 | 初始历史不触发，自己/未知方向/系统消息不触发；无关成员不取消合法任务且不进入模型输入 |
| G1-05 | 不同成员同文不对齐、复用raw key时分存两消息、未知身份后补齐只触发一次 |
| G1-06 | 原群独占发送队列、AI辅助只为草稿、两个成员独立run/发送guard、DemoBridge与demo worker元数据透传 |
| G1-07 | 不同群同名成员与私聊scope互异；实际prompt只读同群同成员记忆，不含另一成员/另一群/私聊marker |
| G1-08 | 成员名单/@选项/本人群昵称/监听/模式/Agent配置6类变更使旧回复失效，同成员新触发可使旧任务过时 |
| G1-09 | 群即使同Agent+listening+ai_auto也不进入公共定时任务eligible |
| G1-10 | 原定时/私聊AI/API/store/快照/上下文/原关怀/图片/连续消息/runtime回归见下 |

## 缺陷与修复证据

- 独立审查发现CheckBox正文名前缀被推为sender：backend移除推断；ListItem/CheckBox两个伪装测试通过。
- 独立UIA测试发现引用mention实体与嵌套message_body误触发：两项初测失败，backend按引用祖先过滤后复测通过。
- 行内中文引号引用中的@已加入反例并通过。
- 未知sender/mention后补齐同一新消息未触发：独立初测1 failed、同raw key跨成员1 passed。总协调修复为合格消息才创建事件，并转述精确两用例复测2 passed；最终群完整回归由总协调运行。
- 新增demo worker测试最初错误假设结果含ok字段，已按既有result/error协议修正后通过。这是测试错误，非产品缺陷。

## 已实际运行

独立完整首轮：

```powershell
& ./pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_group_chats.py -q
```

当时集合 **66 passed，1既有httpx弃用warning，23.20s**。后续新增同raw key、证据富集、实际记忆隔离、bridge/demo边界共8项，全部已执行分批检查；证据富集修复后由总协调执行回归。

后补实际记忆隔离：`-k prompt_reads_only`：**1 passed，68 deselected，1.53s**。

后补原群bridge/demo守卫：`-k 'window_guard or demo_group_bridge or serial_demo_worker'`：**5 passed，69 deselected，0.56s**。

最终测试文件冻结后，本角色再次执行完整群测试命令，实际结果 **74 passed，1 warning，36.64s，退出码0**。包括未知身份后补齐仅触发一次、同raw key跨成员、真正的模型记忆输入隔离和demo worker。唯一warning为既有FastAPI/Starlette TestClient的httpx弃用提示。

总协调先前运行期间测试文件发生了demo worker断言修正，曾报告73通过、1个旧断言失败；此次冻结后的完整74项通过覆盖该版本错配，不将前次转述统计相加。

## 旧模块回归

已执行：

```powershell
& ./pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_agent_schedules.py tests/test_ai.py tests/test_ai_api.py tests/test_store.py tests/test_snapshots.py tests/test_conversation_turns.py tests/test_agent_context_isolation.py tests/test_companion.py tests/test_image_delivery.py tests/test_image_requests.py tests/test_multimodal_turns.py tests/test_runtime.py tests/test_api.py -q
```

实际结果 **174 passed，1 warning，159.99s（2分39秒），退出码0**。包含上一功能的46项Agent定时任务测试和128项既有私聊、AI API、Store、快照、上下文隔离、原关怀、图片、多模态连续消息、runtime与面板API测试。此次群功能没有破坏这些已覆盖行为。

## 群技能、生图及个人功能边界补充

总协调补充群任务跳过图片快捷路由和通用新闻预分类，保证群中的新闻技能能先加载并使用指定数据源；同时群任务过滤个人set_reminder、关闭自动TTS、联系人关怀API拒绝群。仅执行相关新增点，未重复扩大旧回归。

```powershell
& ./pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_group_chats.py -q -k 'group_news_skill or same_group_members or filters_personal_reminders or care_schedule_api'
```

实际结果 **5 passed，74 deselected，1 warning，5.76s，退出码0**：

- 使用实际daily-news-60s技能正文，假模型按use_skill→read_web→generate_image顺序运行，一次返回假PNG。图片仅原群入队，调用真实image_delivery.deliver和假image bridge，media scope归属检查通过，读取的字节一致。无关群与私聊没有消息。
- 新闻源返回模拟失败时不调用生图，不虚构新闻或卡片，只在原群排队明确的来源失败文字。
- 同一群的张三与李四分别触发，scope和group_sender互异，双方上下文和模型输入不混；之前实际记忆输入隔离测试也已验证同群另一成员记忆marker不能进入当前成员prompt。
- 即使绑定set_reminder且auto_voice=true，群任务快照移除个人提醒工具，完成后不调用假TTS，只排队文字。
- 联系人关怀API针对群返回409和“只适用于私聊”，不创建日程。

最终独立验收结论：群监听79项分批通过（先前冻结完整74项加上述新增5项），旧模块回归174项通过，共253项实际执行的不同测试。没有未修复的已知测试失败。唯一warning是既有TestClient依赖弃用提示。真实微信适配未验证的限制仍保持，不因模拟通过而改变。

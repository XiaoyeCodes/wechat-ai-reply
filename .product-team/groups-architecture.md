# G1 群聊监听架构

2026-10-19。复用现有独立微信聊天窗口、串行 worker、消息快照、AIService 和发送核对。后端桥接开发负责 `bridge.py`、`runtime.py` 与新 `group_bridge.py`；数据/AI/会话隔离由总协调实现，前端独立实现。

## 数据合同

contact 扩展 `chat_type:direct|group`、`group_members:list[str]`（空表示所有）、`group_mention_only:bool`（默认 true）、`group_self_name:str`。桥接核验还返回 `group_identity_verified`、`group_members_verified`、`group_self_name_verified`、`group_self_name_source` 和 `group_member_count`。

群 `wechat_id` 是 `group:` 加 `sha256(account_id + NUL + 完整群名)[:32]`，只是工作台内部绑定 ID，绝不冒充微信真实群号。`account_id` 仍来自真实登录身份核验。群改名会使旧绑定停止，需重新核验；多个完全同名会话无法唯一选择时拒绝添加。

`group_self_name_source=uia_settings` 表示由本群信息字段取得，`user_config` 表示用户明确填写“本人群昵称”。二者均可作为已确认的匹配配置，但不把用户填写谎称为自动读取的微信身份。若 UIA 自动取得的本人群昵称与用户配置冲突，阻止保存。

消息扩展 `sender`、`sender_verified`、`mention_me`、`mention_verified`、`mention_source`。`mention_source` 可为 `uia_entity` 或 `visible_text`，演示环境为 `demo`。正文与身份分离，unknown 不触发。

## 核验与观察

`WeixinBridge.inspect_group(lookup,group_members=None,group_self_name='',account_id=None)` 与 worker `op=inspect_group` 为添加/重核入口。完整名称精确搜索、拒同名结果、验证群人数 UIA 标签、当前账号及群窗口类型，然后读取群信息与成员名单。成员 grid 不完整时，打开同一已核验群的成员标签，仅读取完整成员名单，不遍历或导出消息记录。群成员重名不能授权指定成员；观察时已知重名身份也不触发。

sender 仅来自当前消息的独立 sender UIA 标签或带名称的头像控件。不从 `张三:`、`张三\n正文` 等消息文本猜身份；未经独立结构证明的 CheckBox 前缀同样不可信。引用子树内的 sender、mention、body 全部排除。

mention 优先独立实体节点。否则仅在可信 sender 的独立 UIA 正文中按完整 `@本人群昵称` 与边界识别（空白、U+2005、标点、行末）。邮箱、@他人、同名前缀、Markdown 引用行、行内成对引号与微信引用子树不匹配。这是界面可见提及识别，不宣称取得微信私有协议元数据。

每条群消息生成包含可信 sender 与正文的 `identity_text`，并纳入 raw key 摘要。防止 Qt 控件 ID 回收及不同群成员同文被对齐为同一条。快照/数据层的 sender 对齐和按成员会话范围由总协调实现。

## 发送与运行

群回复继续投递到原群 contact，不打开成员私聊；文本、图片、语音、文件沿用既有发送和气泡核对。绑定、监听、发送准备均复核账号、完整标题和群/私聊类型；每次最终发送点击再复核原群，图片粘贴也在可能发生发送之前复核。

`runtime.configure` 看到群规则变化会解除旧监听窗口引用并重新连接。群演示注入接受 `sender` 和 `mention_me`，只用于演示 worker，真实模式继续禁止注入。

## 证据与未验证项

已在假 UIA 树验证 sender 伪装、邮箱/名称边界、引用实体/嵌套正文、内部 ID/账号/类型核验；旧 bridge/runtime 57 项回归通过。未指定真实群名，因此未读取任何真实群或发送消息；实际微信版本的独立 sender 控件、群设置字段和成员列表控件可访问性尚待用户添加具体群时验证。控件证据不足时停止/unknown，不用正文猜测作为补救。

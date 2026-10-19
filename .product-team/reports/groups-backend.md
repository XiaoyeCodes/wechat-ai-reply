# G1 群聊桥接后端交付

2026-10-19。架构与数据合同见 `.product-team/groups-architecture.md`。

## 实现文件

- `wechat_panel/group_bridge.py`：内部群绑定 ID、群 profile 核验、完整成员唯一性、群设置/成员标签读取、独立 sender 证据、可见 @ 提及识别和引用排除。
- `wechat_panel/bridge.py`：真实与演示 `inspect_group`、群类型安全搜索和绑定、群消息元数据、sender/正文复合身份、发送准备/点击/图片粘贴前原群复核。
- `wechat_panel/runtime.py`：串行 `inspect_group` 请求、群规则变化重新绑定、演示群消息 sender/mention 注入。

未修改 store/app/AI/会话模块、前端或测试文件；这些由总协调和对应负责人集成。

## 验证

- `pywechat-main/.venv/Scripts/python.exe -m compileall -q wechat_panel` 通过。
- `pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_bridge.py tests/test_runtime.py -q`：57 passed（11.91s）。
- `pywechat-main/.venv/Scripts/python.exe -m pytest tests/test_group_chats.py -q -k 'not snapshot'`：初始纯桥接版本 20 passed；后续测试 Agent 扩充后，由独立测试 Agent 负责完整 G1 验收。
- 独立测试发现引用树内 mention/body 曾被误识别，现已加入祖先引用树过滤；成对行内引号、Markdown 引用行也排除。
- 曾在总体 AI 接线期间混跑新增 group AI 用例，出现未接线项失败；这些不是最终验收结果。桥接自身唯一旧 fake 兼容失败已修复，随后旧桥接与 runtime 全过。

## 运行边界

没有读取任何真实群，也没有发送任何真实测试消息。只有用户实际添加具体群时才执行目标群核验，成员列表读取为必要核验动作，不导出群消息。实际微信版本是否暴露独立 sender、群信息/成员控件尚未用真实群验证；不满足证据时停止或标记 unknown。

@ 提及支持 `uia_entity` 和 `visible_text`；后者只说明界面文字与配置匹配，不宣称取得微信协议元数据。本人群昵称允许用户明确配置，source 如实标为 user_config。指定成员显示名重名时拒绝授权，完整名单中同名 sender 也不会触发，避免混用会话记忆。

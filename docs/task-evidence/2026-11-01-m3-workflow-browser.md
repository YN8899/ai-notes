# M3 真实浏览器与恢复流程

日期：2026-11-01，Asia/Shanghai。全部数据为明确标记的合成账号、文字、PNG、附件；Browser plugin not available，使用已有 Playwright + Edge。实际运行项目 App、FastAPI、PostgreSQL 和社区 Worker，没有 API 响应替身、真实模型、OSS 或公网操作。

## 已完成的交互证据

| 流程 | 已通过断言 | 机器结果 |
|---|---:|---|
| 桌面1440px/390px：工作台、申请/接受、好友隐私、分享/撤销、私聊/回复/附件、已读、重连、退出 | 13 | `.local/m3/social-chat-browser-results.json` |
| 桌面1440/1280px及390px：原创、独立图片发布、原件下载、删除私人来源后读取及编辑、评论/点赞/收藏/举报、审核下架/恢复、撤回、筛选、账号ZIP下载 | 12 | `.local/m3/community-browser-results.json` |
| 200条离线历史：新发201不越过未加载内容标已读；消费历史后自动收到202；工作台分享入口 | 3 | `.local/m3/chat-boundary-browser-results.json` |
| 超过首30位好友的分页选择、空JSON合法纯文本只读分享 | 2 | `.local/m3/share-boundary-browser-results.json` |
| 浏览器实际下载ZIP→新空库真实恢复→普通账号登录：历史/图片/原件字节/无授权复活/移动布局/全文/退出 | 7 | `.local/m3/recovered-history-browser-results.json` |
| 第三位非好友成员：阅读已发布副本、不创建好友关系、发布/删除本人评论、点赞 | 4 | `.local/m3/third-member-browser-results.json` |

以上共41项本轮机器断言，分属不同数据集/尺寸，不重复累加同一脚本的多次重跑。它们覆盖实际交互与API/对象状态；不是41个独立页面或41次云端验收。

社区草稿重新读取的边界使用 Playwright 延迟真实请求后继续发送，验证等待期间输入禁用，没有伪造响应。删除私人来源的步骤通过实际已认证 API 操作合成笔记；公开副本依旧通过真实浏览器重新读取。

实际账号包为 `.local/m3/browser-account-v4.zip`。该次社区数据集在此前好友私聊流程后重新初始化，因此它不含此前聊天资料；完整消息及收到的附件恢复另由 `test_social_archive.py` 覆盖，不能混称同一份账号包。

恢复使用单独命名的空库及空目录，未启动恢复环境Worker。原件下载的 SHA256 在恢复前后相同。恢复账号仅一个普通用户，活跃好友/授权/线程/帖子/实时票据均为空，历史保留本人阅读能力。

## 截图与视觉检查

主执行者已查看只读分享、手机私聊、社区图片阅读、推荐筛选、恢复历史截图；对应机器检查包含图片 `naturalWidth`、390px `scrollWidth <= innerWidth`、下载内容摘要及退出后私人元素消失。截图保存在 `.local/m3/`：`mobile-shared-reader.png`、`mobile-private-chat.png`、`mobile-community-post.png`、`mobile-community-feed.png`、`mobile-recovered-history.png`、`desktop-community-moderation.png`。

这些是桌面浏览器及移动尺寸验证，不表示真实手机触控、长期双设备运行或公网 M3 已验收。应用版本、服务器和日常开发库仍以各自环境的实际记录为准。

## 最后个人设置交互

另对个人资料完成两项实际请求延迟回归：重新读取期间锁定全部编辑字段；PATCH保存未结束时禁止重新读取，成功后显示值与服务器版本一致。先复现可编辑输入被覆盖RED，再修复复验GREEN。证据 `profile-reload-red.json`、`profile-reload-probe.json`、`profile-save-reload-results.json`。这些是2项额外回归，不把重复执行的核心流程再算一次。

# T27/T28 社区后端执行与审查交接

> 本文为执行阶段交接快照，下方分轮用例数与待办保留原时点。最终冻结源码已完成1107项后端全量、真实浏览器和独立总审计技术核验，最新状态见[M3完成审计](2026-11-01-m3-completion-audit.md)。

日期：2026-11-01，Asia/Shanghai。仅本地开发；未提交、推送、部署、读取真实凭据或调用真实 OSS/模型。实现位于 `backend/app/modules/community/`；冻结迁移为 `c37f47dae123`，父迁移 `b26f36c9d012`。

## 已实现行为

- 原创文本或本人的文字/图片/PDF 页面笔记建立草稿；校验来源所有者和 revision，净化富文本私人图片链接及任意 metadata，保留受控公式、代码和外部链接。
- 草稿与已发布快照分离：编辑草稿不会改变公众所见；显式 publish 固定版本/收据/来源，加入 `community.publish` 持久任务。相同 request_key/参数返回原回执，冲突参数409。
- 每页阅读图与原图有独立 `community/` 对象键、大小和 SHA256。读取源与写入副本均做完整性验证，全部成功后在有效 Worker 租约事务内原子切换 live snapshot；部分复制失败不得公开，自动重试耗尽后可以显式重试。
- 任务旧 token、租约过期、撤回、下架、作者停用均不能完成发布。用户撤回可以取消在途任务；有旧 live 的失败重发保留原 live。
- 已发布快照不依赖原笔记与原图存活。公众接口没有原 note_id、原 file_id、原 object_key、凭据或私人历史；附件每次检查当前帖子可见状态和作者 active。
- 学科、四种帖子类型、自由标签；最新、关注与推荐 feed 使用账号/筛选绑定游标。推荐按冻结的内容完整度规则评分（含独立图片2分、正文至少200字1分），同分按发布时间，公开解释规则，不冒称个性化或 AI 推荐。
- 评论、点赞、收藏、举报持久化；点赞/收藏幂等，评论/举报请求标识防重复和异内容重用；只能删除自己的评论。撤回/下架/停用作者的内容不出现在 feed、收藏、评论或附件直链。
- 管理员审核已公开社区内容与举报，理由和动作进入审计；不能读取作者私人草稿、原笔记或 AI。下架不能通过作者重新发布/撤回绕过。

## 代码与接口

模型：`CommunityPost`、`CommunitySnapshot`、`CommunityAsset`、`CommunityComment`、`CommunityReaction`、`CommunityReport`，共6张新表。公开/草稿响应和字段以 `schemas.py` 为准。

API 前缀 `/api/v1/community`：

- `GET/POST /posts`，`GET /posts/mine`，`GET/PATCH /posts/{id}`（PATCH改草稿），`GET /posts/{id}/draft`。
- `POST /posts/{id}/publish` `{revision,request_key}` 返回202；`POST /posts/{id}/retry`、`/withdraw` 携带 revision。
- `GET /posts/{id}/assets/{asset_id}?kind=reading|original`，同源鉴权，不暴露存储地址；original 是独立副本下载。
- `GET/POST /posts/{id}/comments`，`DELETE /comments/{id}`；`PUT/DELETE /posts/{id}/like`、`/bookmark`；`GET /bookmarks`。
- `POST /posts/{id}/reports`；管理员 `GET /moderation/reports`、`POST /moderation/reports/{id}/resolve`、`POST /moderation/posts/{id}`。

Worker 沿用现有 heartbeat 分支，`community.publish` 调用 `publishing.process_job(claimed)`；该函数自行在租约事务中完成业务及 Job，不使用通用自检 finish。

## 验证证据与未完成项

- 本执行者使用项目 `backend/.venv/Scripts/python.exe -m py_compile` 编译新增模块、迁移和测试，PASS。
- 主执行者反馈：初次合并运行 `test_community.py + test_chat.py + test_social.py` 为 **77 passed，1项既有弃用 warning，51.37秒**；迁移到 `d48f58ebf234` 后 Alembic check 无漂移。本记录引用主执行者结果，不冒称由本执行者独立重跑。
- 初次收集之后补充3项边界测试：撤回后所有公共读写入口、跨帖资产ID及同长度副本损坏、冻结后同长度源图损坏。它们需要随后增量/全量运行，不能继承初次77项通过。
- 后端测试覆盖显式发布、公开/草稿隔离、原回执、编辑不改旧 live、历史快照、来源所有者/revision、独立原图/阅读图、复制失败/重试、旧租约、撤回取消、推荐分页/筛选、互动幂等、举报审核、作者/评论者停用以及并发点赞。
- 此处是实现者自查和交接，**独立规格/质量审查、账号/整站恢复和最终桌面/移动实际 App 闭环仍由主执行者继续安排**；不能据此宣布整个 M3 完成。

## 数据保护接入注意

`community_assets` 的两个目标键是独立副本，ready 必须存在并匹配哈希；pending 的目标可能尚未写入。`source_object_key` 与 `source_original_object_key` 在复制准备/失败时保留用于明确重试，全部发布成功才置空。备份和引用保留必须覆盖仍非空的冻结来源；恢复时将 preparing 快照与 queued/running Job 终态化，不自动继续发布。个人归档由独立任务转换为不可复活授权的离线历史，不能导入后自动公开帖子、关系或通知。

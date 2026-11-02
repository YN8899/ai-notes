# M3 Completion Implementation Plan

> **For agentic workers:** Use subagent-driven-development task-by-task, with independent specification and quality review. Track completion below. User authorized continuous local execution; no additional design approval or Git commit is required.

**Goal:** 完成总架构T25–T29的好友分享、持久私聊、独立社区副本、互动审核和综合工作台，并通过数据保护、权限及浏览器闭环验收。

**Architecture:** 复用现有FastAPI模块化单体与React组件。social提供关系/分享/聊天服务；community提供帖子/副本/互动服务；共享入口由主执行者串行集成，隔离库由一个执行者独占。所有业务资源都在服务端验权。

**Tech Stack:** 已锁定React19/TypeScript/Vite、FastAPI/SQLAlchemy/PostgreSQL16.8、Storage、Worker、Alembic、pytest、Node tests。

设计：[M3 design](../specs/2026-11-01-m3-design.md)。在当前main内工作，禁止覆盖原有改动、读取真实env、操作公网、调用模型、commit/push。

## 1 T25 关系与只读分享

Files: 新建 `backend/app/modules/social/{models,schemas,service,router}.py`、`backend/tests/test_social.py`、冻结迁移；前端 `frontend/src/features/social/`。主执行者集成models注册/main路由。

- [x] 服务与约束：成员搜索、申请/接受/拒绝/解除、独立关注、资料隐私、好友只读授权/撤销。
- [x] 专用分享文字/图片读取，每次重验；原owner-only API权限保持。
- [x] HTTP专项验证跨账号、自申请、重复、并发、停用/回收站/解除/撤销，拒绝AI/写/导出。
- [x] 桌面/移动好友与分享阅读界面、独立规格/质量审查与修复。

## 2 T26 持久私聊

Files: `backend/app/modules/social/chat_{models,schemas,service,router}.py`、`realtime.py`、`backend/tests/test_chat.py`、迁移；前端 `features/social/ChatWorkspace.tsx`。

- [x] 好友唯一线程、消息seq、request-key重复/冲突、分页补拉、单调已读。
- [x] 有限附件上传、参与者下载、归属与内容限制；权限失效时禁止新发送。
- [x] 一次性短期WS票据、Origin/会话校验、重连HTTP补拉、退出清理。
- [x] HTTP/WS/并发/附件专项与真实UI验证；独立规格/质量审查。

## 3 T27/T28 社区

Files: `backend/app/modules/community/{models,schemas,service,router,publishing}.py`、迁移、`backend/tests/test_community.py`；`frontend/src/features/community/`。

- [x] 原创帖子和私人笔记快照、全部独立对象复制、jobs租约/重试、发布/撤回/revision；原源删除不破坏副本。
- [x] 学科/类型/标签、最新/关注/推荐稳定分页、评论/点赞/收藏、举报/管理员审计与下架。
- [x] owner/非owner/admin/匿名、失败复制、下架直链、私源泄漏与副本独立专项测试。
- [x] 完整社区编辑/发布/互动/审核UI、桌面移动QA和独立规格/质量审查。

## 4 T29 工作台与设置

Files: `frontend/src/App.tsx`、`features/social/`、`features/community/`、工作台组件和现有styles；后台dashboard接口按需在social/router.py提供。

- [x] 整合导航、真实概览计数/未读/待办、跨模块跳转与个人资料隐私设置。
- [x] loading/empty/error/409恢复、分页、账号切换缓存/WS清理，390px无溢出。
- [x] `npm run build` 和 `node --experimental-strip-types --test frontend/tests/*.test.mjs` 通过。

## 5 迁移、归档、恢复和文件保留

Files: `backend/app/modules/exports/`、`scripts/{verify_export,restore_export,site_backup,site_backup_profiles}.py`、`ops/site-backup-profiles/`、`backend/app/modules/files/retention.py`、相关tests。

- [x] 完整新增schema迁移与精确整站profile、当前及旧head兼容、隔离空库迁移循环无漂移。
- [x] 新版账号包覆盖用户数据/附件；跨用户记录为不可复活授权的离线历史；v1/v2/v3仍可验证恢复。
- [x] 真实多账号PG/对象整站恢复、账号恢复与恶意输入验证；未完成发布终态化、旧WS票据不复活。
- [x] 有效副本/附件引用纳入清理/备份对象清单。

## 6 最终验证与交付

- [x] 重生OpenAPI及TypeScript，两次字节一致；依赖锁无新增非必要库。
- [x] `backend/.venv/Scripts/python.exe -m pytest -q`，从backend工作目录执行，专用55435 notes_test：1107 passed/0skip；前端153项与build通过，根工具459项运行/6项Windows条件跳过，真实日志保留。
- [x] 实际App桌面/移动闭环：两成员好友→分享→撤销→私聊/重连/附件→发布→第三成员互动/举报→管理员下架→工作台，区分模拟接口与真实后端证据。
- [x] 独立规格/质量/总完成审计逐条检查T25–T29及数据保护，修复所有必要阻断；最终源码及文档签收PASS。
- [x] 更新README、M3计划/操作/更新恢复文档及docs/task-evidence，明确本地完成与线上/真机边界；本地M3目标完成。

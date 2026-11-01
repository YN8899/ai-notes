# M2 Completion Implementation Plan

> **For agentic workers:** Use subagent-driven-development task by task, with independent specification and quality review. User explicitly authorizes continuing through all M2 tasks without stage approval pauses.

**Status:** 2026-11-01 本地开发与独立完成审计PASS；公网/真模型/真机边界单独保留。

**Goal:** 完成总架构 T19–T24，并连同 T16–T18 形成上传、检索、卡片审核、独立复习、测验、提醒及统计的实际闭环，最后逐项报告。

**Architecture:** 沿用 FastAPI/SQLAlchemy/PostgreSQL、独立 Worker 与 React；学习域模块化，不把源笔记和卡片/测验的进度混在一起。复用现有 AI 单次持久调用和私有来源，所有新增用户内容同步导出与备份。

**Tech Stack:** 项目 `backend/.venv`、React/TanStack Query、现有图像阅读/KaTeX、官方 py-fsrs 适配（T22 开始前核验版本并锁定）。

## 授权边界与已有状态

- 用户已批准此前提出的 T19 范围和全部 M2 顺序，持续执行，不把 T19 完成当作总目标完成。
- main 已有 T16–T18/M1 未提交修改，保留。未获得本轮 commit/push/公网维护授权；不上传 AGENTS.md、不读/复制真实密钥。
- 默认仅合成材料、模拟模型与隔离数据库；此前百炼5元真实联调已结束，不延用为卡片/测验的新费用授权。可完成生产适配与模拟验证，不伪称真实模型/设备/公网已验收。
- 开始实施时schema为 `b81d4f0a6c32`/32表；当前已实现到 `84c6e9b302df`/49表（含Alembic）；测试仅 localhost55432 notes_test（可丢弃），不动 notes_dev/公网原卷。

## Task 1 — T19 卡片数据、四类手动制作与 AI 审核

Files: `backend/app/modules/learning/{models,card_schemas,card_service,card_router,card_generation,card_sources}.py`、新 Alembic 迁移、shared model/router 注册、现有 AI 执行后生成草稿挂钩；`backend/tests/test_cards.py`。

- [x] 先定义 discriminated payload：问答、填空、图片遮挡、公式；draft/confirmed/archived；卡片集 CRUD、乐观并发与账号约束。
- [x] 测试 RED：非法填空/矩形/公式，越权、陈旧版本、批量审核原子性、草稿不成为复习对象。
- [x] 实现手动编辑/预览、来源冻结/历史标签/失效拒绝，复用文字与图片/PDF来源。
- [x] AI 生成复用持久 AiRun/单次调用，不新增自动付费重试；结构校验通过后原子写 draft，不自动确认。支持所选文字/普通图片/单页 PDF，遮挡卡必须有图像来源。
- [x] 增加预览/确认/状态/错误与使用量，后台恢复不重放不确定调用；测试源变化/禁用账号/并发去重。
- [x] 前端 `frontend/src/features/learning/` 卡片集与四类编辑、正反面预览、AI 草稿批量编辑/确认/归档、源图遮挡；接入 App 导航和已有笔记入口。
- [x] 更新 OpenAPI/类型、隔离测试、桌面/移动浏览器 QA、规格与质量审查。

## Task 2 — T20 独立计划与统一复习中心

Files: learning `review_{models,schemas,service,router}.py`，必要 Quiz identity schema，新迁移，前端 ReviewCenter，`test_reviews.py`。

- [x] review_items 使用 note/card/quiz 三个显式外键与恰好一个非空 CHECK、owner 约束；每对象一个有效计划，草稿/归档卡不可激活。
- [x] 创建/暂停/恢复/归档计划，manual/fsrs 策略字段与版本保护、下一到期时间、记录变更原因；确认卡片后建立独立计划，绝不修改源笔记计划。
- [x] 统一到期列表、按类型筛选、打开原对象并复习；临时练习记日志但不改变计划。
- [x] 回归独立进度、并发版本、去重、来源删除/回收站与恢复、UTC及显示时区。

## Task 3 — T21 测验闭环

Files: learning `quiz_{models,schemas,service,router,generation}.py`，新迁移，前端 QuizWorkspace/Attempt，`test_quizzes.py`。

- [x] 支持单选、多选、判断、填空、简答的合法结构；手动编辑/审核及 AI 草稿生成，私有来源跳转。
- [x] 作答 attempt 与 answer 持久化、逐题自动保存和 revision/request_key 并发保护；刷新/断线恢复、不丢已保存答案。
- [x] 确定题本地判分；简答 AI 评估显式触发并保留不确定/待复核状态，允许人工复核；不把测验百分数直接当 FSRS Rating。
- [x] 交卷与重复提交幂等、参考答案保护、来源权限、用量/未知调用与恢复验证；测验可建立独立复习计划。

## Task 4 — T22 官方 FSRS

Files: learning `scheduler.py`、review service、依赖 pyproject/uv.lock/requirements locks、前端评级/排期、`test_scheduler.py`。

- [x] 联网核验官方稳定版本/API，锁定真实 py-fsrs6.3.2及跨平台tzdata2026.5；纯适配22项与官方评级/序列化对照通过，独立规格/质量审查通过。排期集成与前端局部审查已通过，最终全M2验收仍需Task7。
- [x] 统一调度接口支持 manual/fsrs、算法版本/参数与状态持久化、UTC；并发评价只推进一次。
- [x] 手动改到期时间写原因/旧值；切换策略保存历史与旧状态；临时练习不推进。
- [x] 提供卡片/笔记/测验主动回忆评级，不展示缺乏依据的整篇笔记精确掌握概率。

## Task 5 — T23 通知与时区

Files: learning `settings/reminders`、Worker maintenance、现有通知/outbox，前端偏好，`test_reminders.py`。

- [x] 用户 IANA 时区、每日提醒时间与启用/关闭偏好；站内通知，不发送外部消息。
- [x] Worker 持久生成当地当天一次的到期汇总；用户+类型+当地日期唯一约束，事务 outbox；重启/重复调度/改时间不重发。
- [x] 宕机仅补发今天错过且仍有待办的提醒，不补历史多日；DST/午夜/关闭/无待办回归。

## Task 6 — T24 事件、统计与日历

Files: learning `events/stats`、前端活动跟踪/统计日历、`test_learning_stats.py`。

- [x] 学习事件 event_id 去重、活动开始结束/心跳与闲置阈值、资源owner检查、时间/长度边界；前端真正接入阅读/卡片/测验活动。
- [x] 同账号多设备重叠时段合并，有效时长不双计；统计从事件/复习日志/测验作答重算。
- [x] 分开展示阅读、复习完成、主动评价与测验结果；当地日期日历/未来到期计划，阅读不等于掌握。
- [x] DST、多设备、重复/乱序/过期心跳、并发与可复算查询回归。

## Task 7 — 数据保护与完整 M2 验收

Files: exports/service.py、scripts/{verify_export,restore_export,site_backup,site_backup_profiles}.py、版本化账号格式、schema profiles、前端导出校验、各恢复/兼容测试、说明与 M2 证据。

- [x] 新卡片/测验/作答/计划/日志/偏好/学习事件全部纳入新版账号 ZIP，并保留 v1/v2 恢复；不得把用户创作内容称为缓存而排除。
- [x] 每个阶段或最终 schema 的精确整站配置档来自空库实际迁移及 pg_dump/restore；保留旧22/27/28/32表准入；恢复终止未完成 AI/生成/判分、撤销会话，已知用量保留，不自动重新收费。
- [x] 更新前备份、迁移、验证、回退文档；不自动发布公网、不改公网工具快照。
- [x] 完整 backend/frontend/ops 回归、锁文件/类型再生成一致、migration 升降级、实际账号与整站恢复；每项适当测试，不仅验证实现镜像。
- [x] 桌面与移动尺寸：上传→OCR模拟→检索→生成草稿→审核→创建独立计划→测验作答/复核→四档复习→通知/统计/日历，验证迟到响应/退出/来源失效。
- [x] 独立规格/质量/最终整体验收，逐条对照总架构 T16–T24；更新 M2 完成情况、新增功能及真实模型/设备/公网边界；证据齐全才 complete goal。

执行时为每任务写短规格/接口契约，细化必要步骤；除真实外发/发布等未授权动作，不逐项向用户重复索要开始许可。

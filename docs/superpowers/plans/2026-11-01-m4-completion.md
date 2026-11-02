# M4 Completion Implementation Plan

> **For agentic workers:** Use subagent-driven-development task-by-task, with independent specification then quality review. User authorized continuous local execution. Work on currentmain, preserve existing dirty/untracked work, do not commit/push/deploy/call paid APIs.

**Goal:** 完成总架构增强任务T30–T34，包含安装推送、完整私人笔记离线同步、安全自定义模型与工具、个性首页/学习分析、实测后的跨进程实时和队列增强。

**Architecture:** 沿用FastAPI/React/PostgreSQL/Worker；按模块划分实现。迁移串行，隔离测试库一名执行者独占；共享main/models/worker/App由单任务执行者修改，禁止并行实现造成覆盖。独立研究/审查可并行。

**Tech Stack:** React19/TypeScript/Vite、IndexedDB/Service Worker/Web Push、FastAPI/SQLAlchemy/Alembic/PostgreSQL16.8、现有pytest/Node及真实浏览器工具。

设计：[M4 design](../specs/2026-11-01-m4-design.md)。快照与原始证据：忽略目录 `.local/m4/`。

## 0 基线与验收矩阵

- [x] 阅读总架构、README/配置/依赖/认证、M3交付；记录现有main和未提交文件摘要。
- [x] 梳理T30–T34完整边界、外部依赖和真实手机验收要求；先开发可独立部分。
- [x] 用全新临时PostgreSQL容器复核当前66表基线，alembic check无漂移；相关social/chat/jobs59项、前端153项与build通过，不修改日常库或M3恢复材料。

## 1 T30 应用壳、安装与推送

Files: `frontend/public/{manifest.webmanifest,sw.js,icons/}`、`frontend/src/features/pwa/`、`frontend/vite.config.ts`、`frontend/src/{main,App}.tsx`；`backend/app/modules/notifications/`、新增Alembic迁移；`shared/config.py`/models/main/worker/jobs；`backend/tests/test_push.py`、`frontend/tests/pwa.test.mjs`。

- [ ] 先测试安全订阅归属/跨账号/退出/410/重复投递/SSRF和拒绝权限回退，再实现持久模型与API。
- [ ] 采用固定VAPID后端配置及受控endpoint、安全加密库，注册/查询本人当前设备/取消/测试通用通知；响应无endpoint或keys。
- [ ] 通知产生时同事务幂等入队，Worker发送前后核验租约与有效会话，超时退避、永久失效、取消投递；恢复不得重放。
- [ ] 版本化壳缓存、安装/更新界面、主动权限请求和降级、通用push显示及安全点击入口；API不泛化缓存。
- [ ] 项目`.venv`运行 `python -m pytest tests/test_push.py -q`；`node --experimental-strip-types --test frontend/tests/pwa.test.mjs`、`npm run build --prefix frontend`，实际安装/离线壳/拒绝权限验证；独立规格和质量审查。

## 2 T31 完整离线资料与同步

Files: `frontend/src/features/offline/{database,sync,merge,cache}.ts`与离线工作区组件、notes接口集成；`backend/app/modules/notes/sync_{models,schemas,service,router}.py`、迁移；`backend/tests/test_offline_sync.py`、`frontend/tests/offline.test.mjs`。

- [ ] 测试operation重放、事务rollback、同字段冲突/非重叠合并、删除及跨owner、提交后断线；实现本人资料manifest和幂等revision写入收据。
- [ ] IndexedDB owner隔离、显式全部/选择缓存、真实原件/阅读图/PDF、容量/缺失提示、离线重开与退出清理。
- [ ] 持久创建/编辑/组织/页序裁剪/回收站/本地Blob上传队列，依赖顺序和跨标签独占，同步前重验会话，失败可重试且不重复建资源。
- [ ] 三方字段合并和可操作冲突工作区，保留副本/服务器/手工编辑；禁止静默覆盖正文与页序。
- [ ] API专项、纯逻辑Node、真实浏览器断网刷新/重新开App/双标签冲突/切账号/存储失败，独立规格与质量审查。

## 3 T32 安全服务商与真实工具流程

Files: `backend/app/modules/ai/{compatible,network_safety,tools,tool_runner}.py`及schemas/providers/chat服务与worker；`frontend/src/features/ai/`；相关tests和配置example。

- [ ] 全部IP解析校验和固定IP TLS连接的SSRF回归覆盖IPv6/mappedIP/重绑定/redirect/proxy/端口/异常URL；不得只测字符串过滤。
- [ ] 后端声明模型目录/能力/共享凭据，实际兼容聊天路由、流式/取消/用量/旧model标识与历史包兼容；前端可选和连接状态，无browserKey。
- [ ] 有界多轮tool_calls消费与严格参数模型；本人检索/读取/学习汇总经服务验权，显示调用与来源、工具关闭/拒绝/预算/注入用例。
- [ ] 合成TLS/模型替身只证明本地协议；真实模型调用需另取明确授权，未有证据不标真实外部验收通过。完成独立审查。

## 4 T33 首页个性化、高级推荐与学习分析

Files: 本人偏好/分析模型、schemas/service/router与迁移；`frontend/src/features/social/Workbench.tsx`、`features/learning/`、`features/community/`及tests。

- [ ] 顺序/显示/入口偏好可持久恢复默认，并纳入账号导出恢复；多设备revision冲突保留输入。
- [ ] 可关闭个人推荐使用本人授权学习信号与可见社区，逐条理由；下架/撤回/停用即失效，稳定分页。
- [ ] 可复算事实统计与可选显式AI分析运行，资料范围预览、用量/取消/无自动重发，关闭后不发模型。
- [ ] 桌面/390px真实入口、推荐筛选、隐私开关和分析失败/样本不足，独立审查。

## 5 T34 真实基线、跨进程实时及队列

Files: `backend/app/modules/social/realtime.py`、共享事件模块、jobs/worker、`frontend/src/features/social/useChatRealtime.ts`、通知入口、隔离负载脚本与tests。

- [ ] 实测多进程/多socket基线，记录SQL/延迟/队列等待与租约恢复；根据测量选择最小满足方案。
- [ ] 一个监听连接/进程、有界本地扇出、事务NOTIFY、断线/溢出/重启补拉，消息和通知均可跨进程分发，保留HTTP最终事实。
- [ ] 有依据的队列角色/公平领取/并发配置，支持多Worker不重复副作用、任务恢复，不自动重放付费调用。
- [ ] 两个真实API进程、多个socket与Worker kill/recover、SQL和延迟前后比较；独立审查，勿冒称生产容量。

## 6 数据保护、最终回归与交付

- [ ] 串行迁移及回退循环；每新head精确schema profile由两空库+真实dump/restore测量生成；旧M3包真实恢复再升级，原业务数据/对象摘要不变。
- [ ] 新用户数据/偏好/工具历史明确纳入账号包；旧v1–v4兼容；认证与订阅不复活、离线收据不重放、对象引用完整；恶意输入验证。
- [ ] 重生OpenAPI/TypeScript两次一致，项目`.venv`后端全量、前端Node/build、工具CI范围与真实浏览器闭环；冻结before/after文件摘要一致。
- [ ] 独立规格/质量/总完成审计逐项对照全部T30–T34；真实手机安装/推送和真实外部服务证据分别登记，缺失仍是未完成。
- [ ] README、M4计划/操作/更新恢复/证据文档及私密环境示例说明，清理专用测试进程/容器；满足全部目标后才complete goal。

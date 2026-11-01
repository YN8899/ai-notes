> **2026-10-31 阶段更新**：用户已批准 M1 剩余事项延期，M1 核心功能阶段结束，完整验收未完成。M2 的 T16 手动单页/选区 OCR、纠错与来源定位，以及 T17 标题/标签/正文/OCR 关键词搜索均已在本地实现并验证，公网尚未更新。见 [OCR 操作说明](docs/OCR识别与纠错.md)、[搜索操作说明](docs/关键词搜索与来源定位.md) 与 [T17 验证记录](docs/task-evidence/2026-10-31-keyword-search.md)。阶段决定见 [M1 延期与交接](docs/task-evidence/2026-10-31-m1-deferred-handoff.md)，后续入口见 [M2 开发计划](docs/M2开发计划.md)。下方较早状态保留原时点；已运行的监测和备份不因延期而停用。

# 知页 AI 笔记 Web

2026-11-01 M2本地开发与独立完成审计PASS：四类学习卡片、独立复习计划、五题型测验、FSRS四档调度、站内每日提醒、阅读覆盖/学习时长/统计日历，以及学习内容ZIP v3与49表整站恢复。公网仍运行原已发布版本，本轮不自动提交或部署。

入口与按钮见[M2学习功能操作指南](docs/M2学习功能操作指南.md)，发布前检查、迁移及回退见[M2更新与数据恢复](docs/M2更新与数据恢复.md)，进度与验收证据统一见[M2开发计划](docs/M2开发计划.md)。

T18 百炼文本向量与可选语义搜索已在本地实现并用合成资料验证，尚未发布公网。含索引预览、显式构建/激活/回退、语义查询和只读来源；默认关闭，关键词保留。见[配置与操作说明](docs/语义搜索与百炼配置.md)和[本轮证据](docs/task-evidence/2026-10-31-semantic-search.md)。使用新 schema 需要带 pgvector 的 PostgreSQL16.8 镜像，不能仅更新应用镜像。

M1核心阶段结束、其余事项按记录延期；M2已在本地开发并验收完成，应用版本0.1.0。手机和电脑共用React前端，后端为FastAPI，数据保存至PostgreSQL，后台任务由独立Worker执行。

日常使用与按钮位置见[网页操作指南](docs/网页操作指南.md)，涵盖 PDF 导入 / 原件下载、ZIP 完整导出、Markdown / JSON 导出和 AI 回答保存。当前尚无将笔记重新生成 PDF 的按钮。

构建缓存、Linux 监测 / 备份工具离线包及使用方式见[部署提速与离线依赖](docs/部署提速与离线依赖.md)。工作流提交到 `main` 后生效；本地实现与验证不自动更新公网服务。

当前完成度与剩余工作统一见 [M1 状态总表](docs/M1开发与云端验收计划.md#m1-状态总表)。2026-10-28 已将 `c39ed778dc9f25cef6b490f9fd69f42b5e87b2ab` 部署到服务器，完成原库迁移与公共信任 IP HTTPS 切换，当前入口为 [https://47.111.12.85](https://47.111.12.85)。外部 HTTP / TLS（传输层安全）/ 首屏资源验证通过；用户已确认手机和电脑端验收通过。2026-10-29 只读监测已在真实 Ubuntu 核验并启用，逐项业务补证、性能、新版 PDF / AI 数据恢复、证书续期、定时备份及外发通知 / 主机外失联检测仍待完成，完整 M1 未完成。见[切换证据](docs/task-evidence/2026-10-28-public-https-cutover.md)、[实机记录](docs/M1实机与性能验收记录.md)和[监测部署证据](docs/task-evidence/2026-10-29-monitoring-deployment.md)。

## 当前可用功能

- 邀请码注册、登录、退出、一次性密码恢复。
- 管理员创建和停用邀请码，管理成员状态，查看操作审计。
- 数据库会话、Argon2id 密码哈希、CSRF 和 Origin 校验、持久化登录限流。
- 私人资源权限检查；管理员不绕过所有者权限。
- 文件夹、笔记本、标签与私人笔记，自动保存、版本冲突提示、历史版本和回收站。
- Tiptap 富文本、公式、代码块、Markdown 导入与本机草稿恢复。
- 图片笔记批量预览、排序、上传、后台生成阅读图和缩略图、失败重试与缩放阅读器。
- PDF 导入为多页图片笔记，保留原件、显示后台逐页进度，支持断点重试与原件下载；默认 50 MiB / 200 页。
- 私有 OSS 官方 SDK 适配器与权限校验后的图片代理；开发环境可使用本地私有存储。
- 管理员在后端私密配置中统一配置 DeepSeek Key，所有登录成员共用；受控能力表与管理员手动连接测试，网页不显示或收集个人 Key。本机已有真实验证，服务器原配置保留，本次切换未复制本机 Key 或调用模型。
- AI 文字、富文本选段与 PDF 选页 / 整本 / 单页框选对话，支持 Markdown 和数学公式显示；普通图片与 PDF 单页矩形由后端实际裁剪，仅发送裁片。来源支持原页高亮与自动定位，保存及 ZIP 恢复保留选区。富文本选段保留原文与历史版本，点击来源可定位原版本全文中的选段。
- 成功回答可选标题和笔记本保存为可编辑笔记，保留格式、问题、模型与独立来源引用；图片来源保留原图和选区位置，重复保存不覆盖编辑或重建回收站笔记。
- 当前账号完整 ZIP 导出，包含回收站、历史版本、原图 / PDF、变体及 AI 对话来源；附离线完整性校验器，默认 512 MiB 上限。支持命令行恢复到独立空库 / 空目录，见 [完整导出格式](docs/完整导出格式.md) 和 [账号归档恢复演练](docs/账号归档恢复演练.md)。另有 [整站备份与隔离恢复](docs/整站备份与隔离恢复.md) 工具，已通过本机三账号 PostgreSQL / 文件演练；显式选择旧结构 `c2e41a9f3b67` 的 [兼容备份、空目标恢复及恢复后迁移](docs/公网旧版数据库备份与恢复.md) 也已完成本地隔离验证，默认仍要求当前 head。2026-10-28 已完成旧版真实 22 表 / 42 对象备份、隔离数据恢复和主机外副本校验，见[真实 OSS 备份证据](docs/task-evidence/2026-10-28-real-oss-backup-restore.md)；新版 PDF / AI 数据恢复另行验收。网页合并导入仍待完成；定时备份执行器与模板已提供，公网启用待完成。
- 持久任务队列、去重、租约续期、过期回收、退避重试和失败任务人工重试。
- 事务 Outbox 和幂等站内通知投递；可在网页发起后台自检并查看真实结果。
- 定时整站备份执行器与 Ubuntu systemd 模板：默认北京时间 03:00 维护窗口、恢复原服务、保留最近 7 份已验证备份，以及独立失败记录和可选通知接口。代码与隔离测试已提供，尚未启用公网任务或外部推送，见 [定时备份与失败通知](docs/定时备份与失败通知.md)。
- 独立文件引用审计与安全清理工具：默认只预演，保留已登记文件/历史/AI 来源，对超过 7 天的已知无引用残片先归档后清理，支持不覆盖恢复；仅维护窗口手工执行，尚未清理用户或云端数据，见 [操作说明](docs/文件引用保留与安全清理.md)。
- 独立只读运行监测工具：采集主机/容器、数据库连接、任务等待、上传与模型错误及已知用量，保存私密快照与本机告警；未知、过期和未完成采集不能显示正常。2026-10-29 已部署独立工具快照、专用只读角色并启用 systemd timer（定时器），上轮结束约 60 秒后再次采集、180 秒过期；真实多轮采集与独立复验通过。外发通知及主机外失联检测未启用，金额与 HTTP 失败率保留不可用，见 [监测操作说明](docs/只读运行监测与本机告警.md)与[部署证据](docs/task-evidence/2026-10-29-monitoring-deployment.md)。
- 纯公网 IP HTTPS：Caddy 2.11.7、独立端口覆盖和持久证书卷；已完成正式短期证书签发与公网切换，仅公开 80 / 443，旧 8080 已关闭。当前证书至北京时间 2026-11-04 10:39:35 有效，真实续期尚待观察。原 HTTP 开发配置保留，见 [本地配置与隔离验证](docs/IP地址HTTPS本地配置与隔离验证.md) 和[切换证据](docs/task-evidence/2026-10-28-public-https-cutover.md)。
- Alembic 初始迁移、OpenAPI 与生成的 TypeScript 类型、依赖锁文件、Docker Compose 和 GitHub Actions CI。

当前暂用“私有 OSS + 后端图片代理”，无需 OSS 图片自定义域名。配置见 [OSS私有存储与图片代理配置](docs/OSS私有存储与图片代理配置.md)。ECS 已更换为 Ubuntu 24.04，采用 GitHub 获取代码、GitHub Actions 构建、杭州 ACR 个人版分发镜像；通用步骤见 [Ubuntu服务器部署与运维](docs/Ubuntu服务器部署与运维.md)，当前进度见 [M1开发与云端验收计划](docs/M1开发与云端验收计划.md)。真实 OSS SDK 联调与本机网页上传 → Worker 生成阅读图/缩略图 → 后端代理阅读已通过。旧版部署、持久化和手动恢复保留历史证据；本轮已完成新版迁移与 IP HTTPS 发布，登录后业务和完整 M1 仍待验收。共享模型配置已开放，启用方式见 [共享模型与DeepSeek配置](docs/共享模型与DeepSeek配置.md)；AI 文字对话已开放，使用方式见 [AI文字对话与运行恢复](docs/AI文字对话与运行恢复.md)；PDF 选页、整本与单页框选提问已开放，见 [PDF对话说明](docs/PDF选页与整本AI对话.md)。回答保存功能使用与边界见 [AI回答保存为笔记](docs/AI回答保存为笔记.md)，保存基础流程的本机实测已通过。保存直接使用已有回答，不再调用模型；聊天与新保存笔记支持受控 Markdown 和数学公式排版。新增普通图片整图 / 矩形选区提问的用法与限制见同一文档；本机桌面 / 手机尺寸浏览器与真实整图、裁片调用已通过。OCR、复习与社区仍未开放，笔记功能不依赖模型 Key。

## 本地启动

安装 Docker Desktop 或 Docker Engine 与 Compose 插件。整个代码目录解压为 `ai-notes`，在该目录运行：

```bash
python scripts/init_env.py
docker compose up --build -d
docker compose exec api python -m app.cli create-admin --username admin
```

Windows 可将首条命令替换为 `py scripts/init_env.py`。初始化脚本只使用 Python 标准库，生成随机数据库密码，不输出密码。管理员命令交互输入两次密码，没有内置默认账户，也不会开放网页管理员注册。

访问 http://localhost:8080 登录；API 文档位于 http://localhost:8080/docs。管理员创建邀请码后及时复制；好友使用邀请码在注册页面创建账户。每个密码至少 10 位。

网页支持同一浏览器不同标签页分别登录不同账号：刷新保留本页登录，新开或复制标签页需单独登录，退出只影响当前会话。使用及更新说明见 [标签页独立登录](docs/标签页独立登录.md)。

运行一次后台自检，状态依次显示等待、执行、完成；完成后在首页显示通知。自检持续约 3 秒，页面定期刷新状态。若任务一直等待，检查 Worker 日志。

```bash
docker compose ps
docker compose logs --tail=100 worker
docker compose logs --tail=100 api
docker compose stop worker
docker compose start worker
docker compose down
```

正常 down 保留命名数据库卷与本地图片卷；`docker compose down -v` 会删除这些数据。不要对有重要内容的环境使用该命令。

## 开发启动

后端使用 Python 3.12 或以上，前端使用 Node.js 22。先启动 PostgreSQL，创建专用开发数据库，配置 DATABASE_URL。

当前本机已准备 `compose.dev.yaml` 持久化开发库，端口 55433、数据库 `notes_dev`，API/Worker 沿用项目虚拟环境。私密配置已在 Git 忽略路径中生成；管理员创建、三个进程启动、停止与恢复见 [本地开发启动与停止](docs/本地开发启动与停止.md)。它与临时回归测试库分开保存数据。

```bash
cd backend
uv sync --locked
export DATABASE_URL='postgresql+psycopg://notes:你的密码@localhost:5432/notes'
export APP_ORIGIN='http://localhost:5173'
uv run alembic upgrade head
uv run python -m app.cli create-admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

另开两个终端，在 backend 运行 `uv run python -m app.worker`，在 frontend 运行：

```bash
npm ci
npm run dev
```

访问 http://localhost:5173。Vite 将 /api 请求代理到 8000，保留浏览器 Origin。PowerShell 环境变量语法是 `$env:DATABASE_URL='...'` 和 `$env:APP_ORIGIN='...'`。

没有 uv 时，可以创建 Python 虚拟环境后用 `pip install -r requirements-dev.lock`，通过虚拟环境执行 alembic、python 和 uvicorn。

## 原生 PostgreSQL 测试

测试会清空业务表，只能使用隔离测试库。以下 Compose 测试库使用临时内存卷，不与应用数据卷共享。

```bash
docker compose -f compose.test.yaml up -d --wait
cd backend
export TEST_DATABASE_URL='postgresql+psycopg://notes:notes@localhost:55432/notes_test'
export DATABASE_URL="$TEST_DATABASE_URL"
export APP_ORIGIN='http://testserver'
uv run alembic upgrade head
uv run alembic check
uv run pytest -q
```

测试包含最后一个邀请码名额的并发注册，以及多个 Worker 的同时领取。CI 使用真实 PostgreSQL 16 执行全部测试。测试后回到根目录运行 `docker compose -f compose.test.yaml down`。

接口修改后重新生成契约：

```bash
cd backend
uv run python -m scripts.export_openapi
cd ../frontend
npm run types
npm run build
```

生成的 backend/openapi.json 和 frontend/src/api.generated.ts 需要一起提交。CI 会检查差异，防止接口和前端类型脱节。

## 目录与扩展入口

| 目录 | 内容 |
|---|---|
| backend/app/modules/auth | 账户、会话、邀请码、恢复与管理员接口 |
| backend/app/modules/jobs | 后台任务、通知与队列服务 |
| backend/app/modules/notes | 笔记组织、正文、版本、回收站 |
| backend/app/modules/files | 私有存储、上传会话、图片处理与代理 |
| backend/app/modules/ai | 共享模型、文字 / PDF / 图片选区对话、回答保存与来源引用 |
| backend/app/shared | 配置、事务、模型、错误、安全与权限 |
| backend/app/worker.py | 独立后台任务入口 |
| backend/alembic | 可审查、可执行的数据库迁移 |
| backend/tests | 身份、权限、并发、租约与投递测试 |
| frontend/src | 账户、空间概览、管理与任务页面 |
| .github/workflows | 原生 PostgreSQL、构建与契约检查 |
| scripts | 本地配置初始化 |

notes 与 files 模块均检查私人所有者，不把 admin 作为读取私人内容的通行证。后台新处理器必须以 lease 验证的事务写入业务副作用；对非幂等外部 API 的未知结果，另行设计 unknown 状态，不能套用当前自检任务的自动重试策略。

Outbox 将业务变更和事件保存在同一个数据库事务，通知投递与 delivered_at 也在同一事务中完成。本期仅使用站内通知列表轮询，实时 WebSocket 在后续阶段加入。

## 配置与运维边界

| 配置 | 默认与说明 |
|---|---|
| APP_ORIGIN | 必须匹配浏览器访问来源，本地 Compose 为 http://localhost:8080 |
| COOKIE_SECURE | 本地 HTTP 为 false；正式 HTTPS 环境为 true |
| APP_ENV | 本地 development；生产必须同时配置 HTTPS Origin 和 Secure Cookie |
| SESSION_DAYS | 会话 7 天，退出和密码恢复立即撤销相应会话 |
| RATE_LIMIT_REQUESTS / RATE_LIMIT_SECONDS | 每个来源 IP 的认证接口限额，默认每 300 秒 10 次 |
| JOB_LEASE_SECONDS | 默认 30 秒；Worker 按租约续期，旧执行者不能完成新租约任务 |
| DB_POOL_SIZE / DB_MAX_OVERFLOW | 每个进程默认 5 / 5，按总数据库连接预算调整 |
| STORAGE_BACKEND / STORAGE_ROOT | 开发默认 local；API 与 Worker 使用同一目录，Compose 配置共享 filesdata 卷 |
| OSS_REGION / OSS_BUCKET / OSS_ENDPOINT / OSS_PREFIX | 当前杭州私有 Bucket；默认区域 HTTPS Endpoint，专用 ai-notes/ 前缀 |
| OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET / OSS_SECURITY_TOKEN | 仅注入服务端；长期 AccessKey 不填 security token；真实值禁止提交 |
| DEEPSEEK_API_KEY | 仅后端持有的共享 DeepSeek Key，所有登录成员共用；留空时模型服务不可用，笔记功能继续正常 |
| PDF_MAX_BYTES / PDF_MAX_PAGES / PDF_PAGE_TIMEOUT_SECONDS | PDF 默认 50 MiB、200 页、每次打开/逐页渲染 60 秒；详细说明见 [PDF 导入配置](docs/PDF笔记导入与逐页转换.md) |

`compose.yaml` 是本地构建开发配置；`compose.server.yaml` 是从 ACR 拉取镜像的服务器基础配置，使用独立 `.env.server`，仓库默认仍绑定本机 8080。当前公网必须合并 `compose.server.yaml + compose.https.yaml + 私有 guard`，固定项目名 `-p ai-notes-server`，仅公开 80 / 443；API / 数据库无公网映射。具体私有 guard 路径留在本地运维记录；后续操作不能只运行基础配置，以免端口或数据库卷配置退回。服务器原一行端口差异保持，由 HTTPS 覆盖配置关闭 8080。真实新版 PDF / AI 数据恢复、续期和登录后业务仍待验收。

账户恢复由管理员核实身份后生成 30 分钟一次性凭证；新凭证替代旧凭证，成功重置后撤销全部旧会话。管理员自身只能在服务器执行 `python -m app.cli reset-admin --username admin` 恢复。

标准错误响应包含 code、message、request_id。日志不记录 Cookie、密码、邀请码、重置凭证、请求正文或签名 URL。停用用户阻止新访问并撤销会话；不把停用误当成删除用户数据。

## 当前验证范围

以下是各阶段当时的验证记录。后续完成情况与本轮审计结论统一见 [M1 计划](docs/M1开发与云端验收计划.md)，不按旧段落的“尚未”重新判定当前状态。

2026-10-20 可靠性收尾：修复跨标签退出后旧内容 / 草稿重现、用户名 pattern，以及普通图片部分变体失败的残留清理。后端全量 278 项、前端 39 项、配置 6 项与生产构建通过；真实本机验证退出、迟到响应、换账号及原有离线 / 冲突 / Markdown 流程，无新增模型调用。存储或数据库状态未知时保守留存对象，强杀与统一引用 GC 仍需后续；完整导出、新版备份恢复、真实手机与云端完整闭环尚未完成。

2026-10-20 普通图片阶段：后端全量 265 项、前端 11 项、配置 6 项与生产构建通过，无新增数据库迁移。使用两张生成的 1200×800 测试图，现有共享模型整图调用 1 次（返回用量 800 tokens）、右半 600×800 裁片调用 1 次（620 tokens）；整图识别左右两侧，裁片只读取右侧。桌面 1440×1100 / 手机尺寸 390×844 验证反向画框、百分比调整、预览、引用、100% / 125% 缩放及平移后的选区位置、保存编辑与刷新；重排后仍打开同一图片并提示历史版本，后续复验没有新增调用。浏览器无应用异常、HTTP 失败或 Key 泄漏，既有登录 pattern / favicon 提示单独识别。每次选择一张已处理普通图片，选区至少 8×8 像素，阅读图和输出裁片各不超过 8 MiB；每篇最多 200 张普通图片。不支持多图同时提问、多个选区或 PDF 页面框选。共享 Key 仍仅在本机后端私密配置中；真实手机触控、云端部署与完整 M1 继续待验收。

2026-10-20 排版阶段：后端全量 204 项、前端 8 项、配置 6 项和生产构建通过。独立 Playwright + Edge 在桌面 1440×1100、手机尺寸 390×844 验证流式原文转终态排版、保存、编辑、刷新、JSON/Markdown 导出与 PDF 原页；使用合成回答，新增模型调用为零。无新应用错误，既有登录 pattern/favicon 提示单独识别。复杂内容回退原文，表格与远程图片嵌入尚未加入。

2026-10-20 回答保存阶段：开发库备份及增量迁移、Alembic check、后端全量 182 项、前端 5 项、配置 6 项和生产构建通过。桌面与手机尺寸完成保存、丢响应后重试、编辑与来源原页回看；新增模型调用为零。见 [本阶段记录](docs/task-evidence/2026-10-20-save-ai-answer.md)。完整 M1 与云端部署仍未完成。

2026-10-16 PDF 导入：后端全量 110 项、配置 6 项、自动保存回归与前端构建通过。真实本机网页上传中文三页 PDF、Worker 渲染、刷新继续、原页序/缩放、原件下载比对和加密文件提示均通过。使用见 [PDF笔记导入与逐页转换](docs/PDF笔记导入与逐页转换.md)，证据见 [本次验收](docs/task-evidence/2026-10-16-pdf-import.md)；真实 OSS PDF 和 ECS 部署未验收。

2026-10-16：按用户要求改为本机后端统一持有共享 DeepSeek Key；隔离 PostgreSQL 后端回归 94 项、配置脚本测试 6 项、自动保存回归与前端构建通过。桌面和手机尺寸检查通过，真实官方模型列表连接成功，浏览器未持有 Key。聊天生成仍未实现。证据见 [共享 Key 验收记录](docs/task-evidence/2026-10-16-shared-deepseek.md)。

2026-10-14：隔离原生 PostgreSQL 后端 68 项测试通过，Alembic 升级与 check 通过；自动保存延迟请求回归 1 项通过；前端 TypeScript 与生产构建通过。OSS 测试使用替身客户端，不能作为真实 Bucket 验收。富文本编辑器的独立构建块仍有体积提示。

首次真实 OSS 联调因 RAM 用户与 Bucket 分属不同账号收到 `403 AccessDenied`；在 Bucket 所属账号重建受限用户、授权并替换私密配置后，77 字节测试图的上传、大小检查、读取比对、删除与删除后不存在检查全部通过。随后两张生成图片的真实网页上传、Worker 派生图、代理阅读、缩放、页序调整与刷新保留通过；测试笔记保留，6 个对象数据库记录合计 55021 字节。Ubuntu ECS 应用部署、手机真实触控、模型调用、备份恢复均未验收；这些事项按 M1 计划继续。Docker 图片共享卷配置已补齐，但这轮尚未完成整套应用容器构建运行验证。

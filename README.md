# 知页 AI 笔记 Web

M0 基础工程，版本 0.1.0。手机和电脑共用 React 前端，后端为 FastAPI，数据保存至 PostgreSQL，后台任务由独立 Worker 执行。

## 当前可用功能

- 邀请码注册、登录、退出、一次性密码恢复。
- 管理员创建和停用邀请码，管理成员状态，查看操作审计。
- 数据库会话、Argon2id 密码哈希、CSRF 和 Origin 校验、持久化登录限流。
- 私人资源权限检查；管理员不绕过所有者权限。
- 持久任务队列、去重、租约续期、过期回收、退避重试和失败任务人工重试。
- 事务 Outbox 和幂等站内通知投递；可在网页发起后台自检并查看真实结果。
- Alembic 初始迁移、OpenAPI 与生成的 TypeScript 类型、依赖锁文件、Docker Compose 和 GitHub Actions CI。

笔记上传、富文本编辑、AI API 调用、OCR、复习、聊天与社区属于后续阶段。M0 不需要任何模型 Key，不会产生模型调用费用；首页明确标注各阶段开放状态。

## 本地启动

安装 Docker Desktop 或 Docker Engine 与 Compose 插件。整个代码目录解压为 `ai-notes`，在该目录运行：

```bash
python scripts/init_env.py
docker compose up --build -d
docker compose exec api python -m app.cli create-admin --username admin
```

Windows 可将首条命令替换为 `py scripts/init_env.py`。初始化脚本只使用 Python 标准库，生成随机数据库密码，不输出密码。管理员命令交互输入两次密码，没有内置默认账户，也不会开放网页管理员注册。

访问 http://localhost:8080 登录；API 文档位于 http://localhost:8080/docs。管理员创建邀请码后及时复制；好友使用邀请码在注册页面创建账户。每个密码至少 10 位。

运行一次后台自检，状态依次显示等待、执行、完成；完成后在首页显示通知。自检持续约 3 秒，页面定期刷新状态。若任务一直等待，检查 Worker 日志。

```bash
docker compose ps
docker compose logs --tail=100 worker
docker compose logs --tail=100 api
docker compose stop worker
docker compose start worker
docker compose down
```

正常 down 保留命名数据库卷；`docker compose down -v` 会删除数据。不要对有重要内容的环境使用该命令。

## 开发启动

后端使用 Python 3.12 或以上，前端使用 Node.js 22。先启动 PostgreSQL，创建专用开发数据库，配置 DATABASE_URL。

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

测试会清空 M0 的业务表，只能使用隔离测试库。以下 Compose 测试库使用临时内存卷，不与应用数据卷共享。

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
| backend/app/shared | 配置、事务、模型、错误、安全与权限 |
| backend/app/worker.py | 独立后台任务入口 |
| backend/alembic | 可审查、可执行的数据库迁移 |
| backend/tests | 身份、权限、并发、租约与投递测试 |
| frontend/src | 账户、空间概览、管理与任务页面 |
| .github/workflows | 原生 PostgreSQL、构建与契约检查 |
| scripts | 本地配置初始化 |

M1 从新增 notes 与 files 模块开始。使用 require_owner 检查私人资源，不把 admin 作为读取私人内容的通行证。后台新处理器必须以 lease 验证的事务写入业务副作用；对非幂等外部 API 的未知结果，另行设计 unknown 状态，不能套用当前自检任务的自动重试策略。

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

Docker 配置仅绑定本机 8080，数据库和 API 无公网映射。它是开发环境配置，ECS 正式域名、HTTPS、备份与监测部署将在 M1 配置。生产时不使用开发服务器或测试凭证。

账户恢复由管理员核实身份后生成 30 分钟一次性凭证；新凭证替代旧凭证，成功重置后撤销全部旧会话。管理员自身只能在服务器执行 `python -m app.cli reset-admin --username admin` 恢复。

标准错误响应包含 code、message、request_id。日志不记录 Cookie、密码、邀请码、重置凭证、请求正文或签名 URL。停用用户阻止新访问并撤销会话；不把停用误当成删除用户数据。

## 当前验证范围

前端生产构建、TypeScript 编译、OpenAPI 类型一致性检查和依赖漏洞检查已通过。数据库迁移与 16 项功能测试在嵌入式 PostgreSQL PGlite 环境通过，包括真正终止 Worker 进程后由新进程接管任务且不重复通知；该环境使用临时测试驱动，未替换交付工程的 PostgreSQL 与 psycopg 配置。

2 项原生多连接并发测试在此环境跳过，已配置到真实 PostgreSQL 的 CI。Docker 构建运行和手机电脑浏览器操作仍需在目标环境验收，不能将 CI 配置本身视为 CI 已通过。

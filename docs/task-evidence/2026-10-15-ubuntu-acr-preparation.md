# Ubuntu / ACR 开发联调准备证据

日期：2026-10-15。本地准备基于初始 M0 `9e94e35`，包含此前 M1 及本轮部署配置。用户已明确授权将这些变更提交并推送 main，CI 通过后手动发布三份镜像到 ACR；云端执行结果以对应提交的 GitHub Actions 记录为准。服务器部署仍由用户分步操作。

## 用户实机反馈

- Ubuntu 24.04、x86_64，约 3.5 GiB 内存、40 GiB 系统盘。
- Docker Engine 29.9.1、Compose v5.6.0，Docker active。
- GitHub 官网 HTTPS 连接超时；SSH `ssh.github.com:443` 经官方 Ed25519 指纹核验和仓库只读部署密钥认证成功。
- `/opt/ai-notes` 已克隆 main，用户截图显示 `9e94e35`、工作区干净。
- GHCR GET 返回 401；Podinfo 公共镜像实际开始下载，但 120 秒限制后退出码 124，未记为完整拉取成功。
- 用户选择 ACR 个人版免费开发联调，创建杭州实例、`ai-notes` 命名空间及 backend/frontend/postgres 三个私有仓库。
- ACR 内网域名 login 返回 unauthorized；使用控制台公网域名后，用户报告 `Login Succeeded`。认证原因尚未进一步定位，当前方案使用公网域名，镜像下载速度待发布后实测。
- 用户自行在 GitHub 添加变量 ACR_REGISTRY、Secrets ACR_USERNAME / ACR_PASSWORD；本轮只查询名称，未读取 Secret 值。

## 配置实现

- 独立 `compose.server.yaml`：只用三个 ACR 镜像；显式迁移 tools profile；web 回环端口；DB/API 不映射端口；内存和日志轮转；保留本机 compose 文件。
- `deploy/server.env.example` 和 `scripts/init_server_env.py`：私有隧道开发 Origin、OSS 模式，随机数据库密码、排他创建和 POSIX 0600 权限，真实 OSS 值留空由用户自行填写。
- `.github/workflows/publish-acr.yml`：main 手动触发，同提交先复用 Project checks，再构建应用镜像和复制官方 PostgreSQL；ACR 凭据只在登录步骤注入；每次运行独立标签；三份 push 成功后汇总 digest；runner 登出。
- Git、backend/frontend Docker context 排除实际 env、私钥等；AGENTS.md 不进入候选 Git 文件。
- Ubuntu 运维文档涵盖首次启动、SSH 隧道、旧版本停写备份、更新、迁移门禁、回退与隔离恢复演练；旧 Windows 云端说明已调整，本机开发方式保留。

## 本轮执行结果

| 检查 | 结果 |
|---|---|
| `backend/.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` | 6 passed；包括 Compose 客户端解析和端口/镜像/内存约束 |
| `docker compose -f compose.test.yaml up -d --wait` | 隔离临时 PostgreSQL 健康；未改动持久开发库 |
| venv Alembic `upgrade head` / `check` | 通过；No new upgrade operations detected |
| venv `pytest -q` | 68 passed，1 个既有 httpx/Starlette 弃用提示，22.76 秒 |
| Node 自动保存延迟请求回归 | 1 passed |
| 前端 TypeScript / Vite build | 通过；富文本独立块仍有约 743 kB 体积提示 |
| OpenAPI 与 TypeScript 类型重新生成 | 当前文件哈希一致，无契约漂移 |
| backend Linux amd64 Docker fresh build | 成功；运行用户 appuser，UID 10001 |
| frontend Linux amd64 Docker fresh build | 成功；Caddy 配置 validate 通过，有既有格式提示及 HTTP 联调提示 |
| actionlint 1.7.12 | 两个 workflow 通过；二进制在临时目录下载并验证官方 release checksums |
| 候选文件秘密标记与排除检查 | 未发现私钥头或真实 AccessKey 标记；backend/.env、.env.server、AGENTS.md 不在候选文件中 |

## 隔离容器冒烟

使用唯一 `ai-notes-smoke-随机后缀` 项目、临时 `.env.server`、本地刚构建的镜像、tmpfs PostgreSQL、随机回环网页端口。临时覆盖 STORAGE_BACKEND=local，不使用真实 OSS；全部步骤完成后执行该唯一项目的 down，未对业务或开发数据库执行删除。

通过：全新数据库迁移 → API readiness → Caddy 静态页面及 API 代理 → 临时管理员创建/登录 → diagnostic 入队 → 独立 Worker 成功执行。未发起真实云资源调用。

首次测试驱动用 Windows subprocess text 模式向 Linux CLI 管道输入密码，注入 CRLF，导致测试密码带额外回车并出现登录 401；输出读取另有 GBK 解码错误。最小复现确认 text 模式末尾带 CR、bytes 模式不带 CR。测试驱动改用 UTF-8 字节流后，完整流程通过；未修改应用认证代码。该问题不等同于服务器交互式密码登录失败。

## 云端 CI 首次运行与修复

`35e6600` 已按授权推送 main。首次 Project checks（运行 `38694006513`）的后端迁移和 68 项测试通过，前端接口类型检查因 HTTP 422 的说明文字差异失败：本机 Python 3.13 默认 `Unprocessable Content`，云端 Python 3.12 默认 `Unprocessable Entity`。Python 官方文档记录了 3.13 的 HTTPStatus 命名调整。

已用本地 Python 3.12 Linux 镜像复现相同差异，并新增接口契约回归测试；修复前失败。应用显式固定 422 说明为现有契约中的 `Unprocessable Content`，不改状态码、错误响应内容或 CI 检查强度。修复后回归测试在本机 Python 3.13 venv 与 Linux Python 3.12 均通过，且 Python 3.12 生成的整个 OpenAPI 与已提交文件完全一致。新提交的完整 CI 与镜像发布结果以 GitHub Actions 记录为准。

来源：[Python HTTPStatus 变更说明](https://docs.python.org/zh-cn/3.13/library/http.html)。

第二次 CI（`436886e`，运行 `38694391789`）的后端、前端构建/类型及部署配置检查均通过；完整 OpenAPI 文件 diff 显示显式 description 使 JSON 属性顺序发生变化。此前只比对解析后的 JSON，未覆盖序列化文本顺序。已重新生成 OpenAPI，导出脚本固定 UTF-8/LF，并新增完整文本一致性测试；修复前失败，重新生成后两项接口契约测试在 Python 3.12/3.13 均通过，前端类型保持无差异。

## 尚未验收

- 真实 GitHub CI 的本次提交运行、ACR 实际 push/pull、服务器应用部署与速度。
- 本轮 Ubuntu 镜像下的真实 OSS/网页联调、重启数据持久化、数据库与 OSS 完整备份恢复。
- 公网域名备案/HTTPS、生产 ACR 方案、基础镜像补丁升级核对。
- M1 既有孤儿对象清理、真实触控和 AI 模型功能待办。

本轮本机验证不能替代上述服务器与付费资源验收。

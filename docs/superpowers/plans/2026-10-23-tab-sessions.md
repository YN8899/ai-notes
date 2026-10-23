# 标签页独立登录 Implementation Plan

**Goal:** 同一浏览器不同标签页独立登录、退出和读取私人数据。

**Architecture:** 采用已确认的 sessionStorage 凭证 + Authorization 请求头。复用数据库会话和权限规则，保留 Cookie 兼容接口，联动前端请求、图片与缓存生命周期。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、React、TypeScript、Node test、Playwright。

## 执行清单

- [x] 1. 新增 `backend/tests/test_tab_sessions.py`：同客户端 Cookie 干扰、双会话注销、CSRF、失效/停用/重置、笔记/图片权限。修改前10 failed，最终全量104 passed。
  - 环境：`backend/.venv` 与 `compose.test.yaml` 的独立 `notes_test`；禁止访问业务库及真实 OSS/模型。
  - 命令：`backend/.venv/Scripts/python.exe -m pytest tests/test_tab_sessions.py -q`（backend 目录，显式 TEST_DATABASE_URL）。
- [x] 2. 修改 `backend/app/shared/dependencies.py`、`backend/app/modules/auth/{router,schemas}.py`：新增不写 Cookie 的 tab 登录/注册/重置；严格解析 Bearer；注销只撤销当前会话；复用原 service。
- [x] 3. 新增 `frontend/src/session.ts` 与 `frontend/tests/session.test.mjs`、`auth-races.test.mjs`；修改 `api.ts`、`App.tsx`：标签页存储、复制页检测、请求头、401 与缓存清理；保留用户已有 UUID 改动。
- [x] 4. 修改 `features/files/{upload,PrivateImage}.tsx/ts`：上传使用本页请求头，图片 fetch + Blob + URL 回收；前端14项回归通过。
- [x] 5. 同步 `backend/openapi.json` 与 `frontend/src/api.generated.ts`，新增使用说明，隔离库全后端104项、Node14项、配置6项、构建、契约与迁移检查通过。
  - 后端：`python -m alembic upgrade head`、`python -m alembic check`、`python -m pytest -q`。
  - 契约：`python -m scripts.export_openapi`；前端 `npm run types`、`npm run build`。
  - 前端：`node --experimental-strip-types --test frontend/tests/*.test.mjs`。
- [x] 6. Playwright 同context多page验证不同账号、新标签页、刷新、退出、原生复制页、延迟退出、私有笔记和真实本地图片上传/阅读，全部通过；保存仓库外证据。Browser plugin not available，沿用已缓存 Playwright，不增加项目依赖。
- [x] 7. 独立review发现3项竞态并修复复核；diff检查通过，AGENTS.md保持忽略；保留既有修改和执行期间新增的外部styles.css修改；清单及evidence已更新；未执行commit/push/部署。

后续发布授权：2026-10-23 用户要求一起提交和推送上述全部功能改动，排除 AGENTS.md、密钥与临时文件；用户选择由 Codex 执行 SSH 更新、本人进行网页验收。具体发布状态以 GitHub Actions 和部署回执为准，本清单记录功能实现时点。

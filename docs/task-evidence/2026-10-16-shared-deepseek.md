# T11 共享 DeepSeek Key 本地验收

日期：2026-10-16。分支：`codex/deepseek-credentials`。按用户新要求取代此前的个人 Key 方案：所有登录成员共用本机后端的 Key，真实值仅保存在本机私密配置中。

## 实现和配置

- `DEEPSEEK_API_KEY` 使用服务端 `SecretStr` 配置，默认空值；成员通过同一份配置获取共享服务，网页没有个人凭据表单。
- 目录接口只返回共享状态和能力表；管理员可测试连接，普通成员无此管理权限。响应不含 Key 明文、掩码或末四位。
- 移除个人凭据 CRUD/测试接口、加密服务、主密钥初始化脚本及 cryptography 依赖；保留已应用的历史迁移和模型以保持数据库兼容。
- 用户提供的 Key 从当前对话本机记录读取到内存，只写入被忽略的 `backend/.env`，未在命令、输出或脚本源码中复制真实值。没有写入根目录 `.env`、服务器配置、GitHub Secrets 或提交。
- API 与 Worker 已重启加载共享配置，API readiness 返回 ok；Worker 在受控终端会话运行。前次重启未加载的问题已解决。

## 检查结果

| 检查 | 结果 |
|---|---|
| 后端全量 PostgreSQL 回归 | 94 项通过，其中共享模型相关 24 项；专用 `notes_test` 库，55432 端口 |
| Alembic upgrade / check | 通过；无新迁移操作，未降级开发库 |
| 根目录配置脚本测试 | 6 项通过；原个人主密钥脚本及其 4 项测试随方案移除 |
| 自动保存回归 | 1 项通过 |
| TypeScript / Vite build | 通过；富文本编辑器原有大块体积提示仍在 |
| OpenAPI / TypeScript 契约 | 同步生成，提交后无差异检查通过 |
| 桌面 1440×1100 | 共享状态、管理员测试连接、能力表正常；无 Key 输入或管理按钮 |
| 手机 390×844 | 管理员和成员布局无页面横向溢出；成员没有测试按钮 |
| 浏览器数据检查 | 请求/响应、DOM、localStorage、sessionStorage 中均未发现真实 Key |
| 浏览器错误 | 本次功能控制台、页面异常、请求失败均为空；缺省 favicon 是基线已知项，在独立 PR #1 修复 |

后端覆盖：未登录拒绝、普通成员与管理员共享同一目录但不能测试连接、个人路径 404、未配置时 503 且基础服务健康、无效配置不回显、CSRF/Origin、固定官方地址和服务端凭据、客户端无法覆盖 Key/地址、受控模型过滤、不入凭据表、审计只含服务商/状态码、401/403/429/5xx/重定向/网络异常脱敏且不重试、无效或过大响应、测试期间退出/停用/降权后拒绝返回管理结果。

pytest 在全局配置中强制清空真实 `DEEPSEEK_API_KEY`，共享测试只用假 Key 和 MockTransport，禁止未经显式替身的网络访问。

## 真实连接与网页测试

通过本机真实 API、现有管理员会话，在网页点击一次“测试连接”。后端使用用户明确授权的 Key 请求官方 GET `/models`，返回：

```json
{"status":"valid","code":"connected","available_models":["deepseek-flash","deepseek-v4-pro"]}
```

这轮只进行一次带 Key 的真实模型列表请求，没有生成调用，没有发送笔记。浏览器中的后续“未配置”状态使用目录响应 fixture；成员页面用角色 fixture 检查呈现，真实成员身份与管理权限由隔离 PostgreSQL API 测试覆盖。未新增开发库成员、个人凭据或测试笔记。

临时 Playwright 脚本和图片在仓库外的当前 Codex 可视化目录：`deepseek-shared-validation.cjs`、`deepseek-shared-desktop.png`、`deepseek-shared-mobile.png`、`deepseek-shared-member.png`。真实 Key 仅在 Node 内存中用于比对泄漏检查，没有作为 evaluate 参数传入浏览器，也没有写入图片或脚本。

## 未验收范围

聊天生成、文字/图片实际推理、SSE、取消、历史、来源引用和回答保存尚未实现；真实模型列表成功不代表这些功能已完成。未迁移共享 Key 到服务器，未部署或合并上游 PR。当前启用说明见 [共享模型与DeepSeek配置](../共享模型与DeepSeek配置.md)。

# 个人模型 Key 与 DeepSeek 配置

本次对应 T11：个人凭据管理、受控模型能力表和连接测试。先接入 DeepSeek；聊天、SSE、笔记上下文和回答保存分别在 T12–T13 实现。

## 两种密钥

| 名称 | 用途 | 保存位置 |
|---|---|---|
| `AI_CREDENTIAL_ENCRYPTION_KEY` | 服务端加密所有用户的模型凭据，随机 32 字节 | 忽略的运行配置或部署 secret；与数据库备份分开保管 |
| 个人 DeepSeek API Key | 本人调用 DeepSeek 的凭据 | 用户在“模型设置”输入；服务端加密后存 PostgreSQL |

两者不能互换。不要把个人 API Key 填到主加密密钥设置，也不要通过聊天、截图、Git 或日志传递真实值。已在聊天中暴露的 Key 应先到服务商控制台撤销，再生成新 Key 填入本地页面。

## 初始化主加密密钥

在仓库根目录执行；目标私密配置文件必须已经存在。脚本只使用 Python 标准库，不显示新值，不覆盖已有非空设置，并拒绝公开模板和仓库外路径。

本机 API/Worker 直接运行、读取 `backend/.env`：

```powershell
python scripts/init_ai_secret.py --env-file backend/.env
```

本地整套 Docker Compose 读取根目录 `.env`：

```bash
python scripts/init_env.py
python scripts/init_ai_secret.py --env-file .env
docker compose up --build -d
```

Ubuntu 私有联调读取根目录 `.env.server`：在按服务器运维文档准备好配置后执行 `python3 scripts/init_ai_secret.py --env-file .env.server`，再按更新流程迁移并重建 API/Worker 容器。现有服务器镜像必须先包含本次代码；本说明不代表已发布服务器版本。

本机原生进程需重启 API 和 Worker 才能加载配置。Docker 的 `restart` 不会重新注入已修改的 env 文件，应使用 `up -d --force-recreate api worker`。不同环境使用各自的主密钥；同一环境的服务实例必须一致。环境变量优先于 `.env`，已有部署 secret 时应维护同一份值。

主密钥允许留空：笔记和账户功能继续可用，模型页面提示管理员尚未配置；保存与测试返回 503，已有凭据仍可删除。填写了非空但格式错误的值会阻止服务启动，避免误用无法解密的配置。

## 数据库迁移和备份

先按现有运维流程备份数据库，再在 `backend` 执行：

```bash
uv run alembic upgrade head
uv run alembic check
```

迁移 `c2e41a9f3b67` 仅新增 `ai_credentials` 表，接续 `0b3b93086d90`。不要通过降级测试回滚真实凭据表；该迁移的 downgrade 会删除表及其中的凭据。

主密钥需单独、保密备份。恢复数据库时必须恢复配套主密钥；丢失或直接更换主密钥后，旧凭据将无法解密，需要用户重新保存 Key。本版只支持加密版本 1，尚无自动密钥轮换工具。密文与主密钥同时泄漏仍会暴露凭据，二者不应放在同一公开位置。

## 网页使用

1. 登录后进入侧栏“模型设置”。本机 Vite 地址为 `http://localhost:5173`，整套 Compose 为 `http://localhost:8080`。
2. 在密码输入框填写新的个人 DeepSeek API Key，点击“保存 Key”。保存成功后输入框清空，页面仅显示末四位掩码。
3. 点击“测试连接”。成功后显示本应用支持且该账户返回的模型；也会明确显示凭据拒绝、限流或网络异常。
4. 替换 Key 后旧测试结果清空，需重新手动测试。删除前页面确认，删除后不能取回原 Key。

Key 不写入浏览器 localStorage/sessionStorage。用户、其他成员和管理员均无法通过本应用接口读取明文；管理员也不能测试或删除他人的凭据。服务端在连接测试时才解密发送给固定 DeepSeek 地址。修改接口复用会话、CSRF、Origin 和 revision 校验，避免多页面覆盖新值。

## 连接测试和能力范围

测试仅向固定的 `https://api.deepseek.com/models` 发送一次 GET，以 Bearer 凭据读取模型列表，不发送笔记、不生成回答、不自动重试、不跟随重定向，也不接受自定义地址。每个凭据两次测试的启动时间至少间隔 10 秒。

服务端限制响应大小、设置连接/读取超时，并只保留受控注册表中的模型 ID；不会把服务商返回的新模型自动开放给用户。连接途中替换或删除凭据、注销或停用账户，会阻止旧结果回写；较早的测试也不能覆盖较新的测试结果。

截至 2026-10-16，本应用的受控能力如下。依据 [DeepSeek 官方模型说明](https://api-docs.deepseek.com/quick_start/pricing) 和 [模型列表接口](https://api-docs.deepseek.com/api/list-models)；服务商后续变化需人工核对注册表。

| 模型 ID | 文字 | 图片 | 流式 | embedding |
|---|---|---|---|---|
| `deepseek-flash` | 支持 | 支持 | 支持 | 不支持 |
| `deepseek-v4-pro` | 支持 | 不支持 | 支持 | 不支持 |

“连接成功”只确认凭据可读取支持的模型列表，不代表聊天、图片输入、余额、额度或生成质量已验收；能力表是后续会话功能的约束，本版尚未开放生成接口。平台公共 Key 没有后端兜底，自带 Key 未配置时不会改用平台凭据。

## 当前验收边界

自动化测试使用假 Key 和 `httpx.MockTransport`，网页连接状态使用浏览器模拟响应；保存、替换、删除走真实本地 API 和开发库，模拟假凭据已清理。没有保存或调用聊天中提供的 Key。

代码验证与真实服务商验收分别记录。有效新 Key 的真实模型列表测试仍待用户在本地页面执行；文字/图片对话、断线取消、去重计费和来源引用属于后续任务。详见 [本次验收记录](task-evidence/2026-10-16-deepseek-credentials.md)。

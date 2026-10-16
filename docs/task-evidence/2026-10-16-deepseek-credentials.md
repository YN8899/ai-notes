# T11 个人模型凭据与 DeepSeek 本地验收

> 历史记录：用户随后明确改为所有成员共用本机后端 Key，个人配置入口已移除。当前实现与真实连接结果以 [共享 Key 验收记录](2026-10-16-shared-deepseek.md) 为准；下文保留当时验证事实。

日期：2026-10-16。分支：`codex/deepseek-credentials`。基于上游 `1948ce2` 独立实现；上一轮用户名 pattern 和 favicon 修复仍在独立 PR 中，本分支未重复混入。

## 本次实现

- 新增 `ai_credentials` 增量迁移、AES-256-GCM 密文存储；随机 nonce，认证数据绑定账户、服务商、凭据 ID 和加密版本。
- 新增认证后的模型目录、凭据列表、保存/替换、删除和手动连接测试接口；响应只包含掩码和安全元数据。
- 复用私人所有者、会话、CSRF 和 Origin 校验；用 revision 保护替换/删除，测试释放数据库锁后访问服务商，并在回写前重新验证账户、会话、revision 和测试启动标识。
- 首家服务商 DeepSeek：固定官方 `/models` 地址、不跟随重定向、不读取环境代理、不自动重试；限制响应大小并过滤为受控模型。
- 前端增加“模型设置”，支持密码输入、保存/替换/删除、测试结果和能力表。平台公共 Key 与聊天生成保持未开放。
- 增加私密主加密密钥初始化工具及配置说明；锁文件、OpenAPI 和 TypeScript 契约同步更新。

## 验证结果

| 检查 | 结果与边界 |
|---|---|
| 后端全量 pytest | 101 项通过，其中本次凭据测试 31 项；仅专用 PostgreSQL `notes_test`，端口 55432 |
| Alembic | 隔离测试库和持久开发库升级成功，`alembic check` 无新增操作 |
| 根目录 unittest | 配置脚本等共 10 项通过 |
| 前端自动保存回归 | 1 项通过 |
| TypeScript / Vite 生产构建 | 通过；富文本编辑器保留既有大块体积提示 |
| OpenAPI / TypeScript | 重新生成；提交后检查生成文件无差异 |
| 桌面 UI | 1440×1100，保存、清空输入、掩码、替换、连接成功与无效状态、删除均通过 |
| 手机尺寸 UI | 390×844，表格可在容器内横向滚动，页面无横向溢出，按钮可操作 |
| 浏览器问题 | 本次流程控制台错误、页面异常、请求失败均为空 |

后端覆盖：明文不在响应、审计及测试日志出现；随机密文和 AAD/篡改校验；管理员无法查看或操作他人 Key；revision 冲突；非法输入及自定义地址拒绝；未配置/错误主密钥；固定请求地址、受控模型过滤、10 秒限额；401/403、429、5xx、重定向、网络异常、无效/过大响应；测试进行中替换、删除、停用、退出和较新测试阻止旧结果回写。

初始化工具覆盖：随机 32 字节密钥、保留其他设置、已有值不覆盖、空值填充、重复设置/公开模板/外部路径拒绝。

## 网页验证方法

临时 Playwright 脚本和截图保存在仓库外的当前 Codex 可视化目录，文件为 `deepseek-settings-validation.cjs`、`deepseek-settings-desktop.png` 和 `deepseek-settings-mobile.png`。

网页保存/替换/删除实际调用本机 API 和持久开发库。脚本先确认账户无已有凭据，只保存明显假的测试 Key；连接测试使用浏览器响应 fixture，演示成功与无效状态，未向 DeepSeek 发出带 Key 的请求。测试结束后已删除假凭据，数据库与页面凭据列表为空。后端连接流程和结果持久化由 MockTransport 的真实路由测试覆盖。

浏览器通过认证 API 建立本地测试会话，避开基线用户名 pattern 的已知问题；这不代替独立 PR 中的登录表单验收。截图里的掩码和结果均为模拟示例，不能作为真实凭据连接成功的证据。

开发库新增表前已完成私有本机数据库备份。真实登录密码、主加密密钥、数据库备份和本机运行配置均在忽略路径，未进入提交。

本机交付状态：开发 PostgreSQL 健康、API 就绪，Vite 与 Worker 保持运行，隔离测试容器已清理。自动审批拒绝了 API/Worker 重启操作，仅返回“策略阻止”，因此最后补充的“较早测试不能覆盖较新测试”保护已通过 pytest，但现有 API 进程仍需按本地启动文档重启后加载；未宣称该保护已在现有进程生效。

## 可复验命令

在项目根目录启动隔离库：

```powershell
docker compose -f compose.test.yaml up -d --wait
```

在 `backend` 配置隔离测试环境（pytest 会清空业务表，禁止指向 `notes_dev`）：

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://notes:notes@localhost:55432/notes_test'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
$env:APP_ORIGIN='http://testserver'
uv run alembic upgrade head
uv run alembic check
uv run pytest -q
uv run python -m scripts.export_openapi
```

在 `frontend` 执行 `npm run check:types`、`npm run build`。根目录执行：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
node --experimental-strip-types --test frontend/tests/autosave.test.mjs
docker compose -f compose.test.yaml down
```

## 尚未完成

- 用户撤销聊天中暴露的旧 Key，换新后在本地页面完成真实模型列表连接测试；本次未保存或使用该聊天 Key。
- T12：独立/笔记会话、SSE、取消、历史、运行去重和中断保留；T13：来源权限、上下文预算、引用与回答保存。
- 有效凭据的真实文字和图片生成，以及完整 M1 云端/跨设备闭环；模拟测试不计入这些验收。
- 主密钥自动轮换和隔离备份恢复演练尚未实现；按配置文档独立备份主密钥，不能直接更换后期待旧密文仍可用。

完整启用说明见 [个人模型Key与DeepSeek配置](../个人模型Key与DeepSeek配置.md)。

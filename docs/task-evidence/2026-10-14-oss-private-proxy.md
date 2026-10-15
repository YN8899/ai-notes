# 私有 OSS 与图片代理阶段证据

日期：2026-10-14。用户确认暂采用私有 OSS、后端权限检查与图片代理，暂缓 OSS 自定义域名和浏览器直传。对应 `M1开发与云端验收计划.md` P2；不是完整 M1 或云端上线验收。

## 当前实现

- 官方 `alibabacloud-oss-v2==1.4.0`，区域 HTTPS Endpoint、V4 签名、受控项目对象前缀；凭据仅在服务端使用。
- 浏览器认证上传会话、大小与读取时限、Worker 验图与派生图、短期读取凭证与每次所有者检查。
- 同源图片内容返回，明确 `inline`，不转发 OSS 强制下载响应头；云端故障返回脱敏错误。
- 前端批量选择、预览调整、上传进度、成功项保留、失败处理重试、缩略图与缩放阅读器。
- 开发 Compose 增加 API/Worker 共享私有图片卷与非 root 写权限。该卷配置通过结构检查，尚未整套构建运行。

## 阶段自动化回归（此前已执行）

后端使用项目 `backend/.venv` 与 `uv`；测试库为 Compose 独立临时 PostgreSQL `notes_test`，端口 55432。

```powershell
# backend，TEST_DATABASE_URL / DATABASE_URL 指向隔离 notes_test
uv run alembic upgrade head
uv run alembic check
uv run pytest -q

# frontend
npm run build
node --test tests/autosave.test.mjs

# 根目录，仅读取示例配置
git diff --check
docker compose --env-file .env.example config --no-env-resolution --quiet
```

- 后端 68 项通过；Alembic check 没有新增操作。
- 前端 TypeScript 和生产构建通过；慢保存自动保存回归 1 项通过。
- diff 与 Compose 配置检查通过。
- 后端测试有 Starlette/httpx 弃用提示；构建有富文本独立块约 743 kB 的体积提示，均不是失败。
- OSS 测试使用替身 SDK 客户端，覆盖写入与读取、对象前缀、地域地址、响应体关闭、读取上限、脱敏错误、权限与代理响应头。未调用真实 OSS。

## 当前浏览器验证

通过浏览器插件连接本地 Vite 与 API，使用独立 `notes_ui` 库、`.local/qa-files` 和合成测试图片；不使用真实用户数据或云端资源。

- 登录 → 新建图片笔记 → 选择两张 PNG → 调整预览顺序 → 批量上传 → Worker 完成 → 缩略图与阅读图成功显示。
- 按钮放大至 125%，页面顺序调整成功，服务器 revision 增加；刷新后笔记、页面与顺序保留。
- 图片 DOM 来源均为 `/api/v1/files/.../content`；所有三张可见图片加载完成。证据中不记录读取凭证。
- 1440px 桌面与 390px 手机视口截图已检查；手机 `scrollWidth=innerWidth=390`，没有页面横向溢出；控制台 error 列表为空。
- 手机真实双指触控、图片拖动以及完整富文本操作未在本轮实机验收。

截图保存在工作区外的本轮 visualization 目录，文件为 `image-proxy-desktop.jpg` 与 `image-proxy-mobile.jpg`，不提交合成文件或截图到 GitHub。

## 未完成边界（首次云端联调前）

真实 Bucket、RAM 凭据、ECS 运行、域名入口与真实手机验收未执行。未执行 commit、push 或部署。配置后的小规模云端联调另行确认。

图片处理部分成功后崩溃产生的孤儿派生对象清理尚未补齐；Bucket 版本控制的历史数据计费与生命周期应单独处理。模型调用、数据导出和备份恢复仍按 M1 后续计划推进。

## 后续：用户授权的首次真实 OSS 联调

用户已创建并授权 RAM 用户，在忽略的 `backend/.env` 中自行填入凭据，并明确回复“同意联调”。检查只输出字段存在与匹配状态，未显示密钥；实际配置字段完整、杭州地域、Bucket 与项目前缀匹配。

仅针对 `ai-notes/connection-check/3834b1a8-0778-4bf1-acdf-b9429d2591db.png` 尝试上传 77 字节的合成 PNG，随后尝试清理该测试路径；上传和清理均失败。对同一路径的 HEAD、GET 诊断收到真实 OSS `403 AccessDenied`，EC 为 `0003-00000001`，错误类别涉及 Bucket ACL；没有返回可用的 AccessDeniedDetail 权限判定细节。

真实连接已到达 OSS，但上传、读取、删除权限尚未验收通过。上传未获成功确认，清理也未成功，不能声明对象不存在或已删除；权限纠正后需要核对该测试路径。下一步核对凭据对应的 RAM 用户、策略绑定及当前策略资源范围，不扩大 Bucket 公共权限。原始异常、密钥与签名请求均未写入输出或本文。

参考：[OSS 错误 0003-00000001](https://help.aliyun.com/zh/oss/user-guide/0003-00000001)。

后续核对：用户截图中的策略允许 `GetObject`、`PutObject`、`DeleteObject`，资源为本项目 `ai-notes/` 前缀；策略授权对象为 `ai-notes-storage`，范围为账号级别。`Resource` 单字符串属于有效写法，摘要 Beta 的异常提示不能单独作为结论。

请求层诊断只输出布尔值：发出的请求使用 OSS V4，Credential 与当前配置匹配，HTTPS Bucket Host、对象路径和 TLS 验证均正确；经过代理与单次直连均返回同一 `403` / EC。该诊断的代理绕过仅在单次进程请求内进行，未改系统代理或应用持久配置。

官方只读 `GetCallerIdentity` 返回 HTTP 200，身份为 RAMUser；与截图中创建用户的账号及 `ai-notes-storage` ARN 完全匹配。只输出匹配布尔值，不输出凭据、请求 URL 或完整身份标识。这证明当前凭据可完成身份认证，仍不能证明具备目标 Bucket 的对象权限；下一步核对 Bucket 归属和云端权限判定。

参考：[GetCallerIdentity](https://help.aliyun.com/zh/ram/developer-reference/api-sts-2015-04-01-getcalleridentity)、[RAM 免费计费说明](https://help.aliyun.com/zh/ram/product-overview/billing-methods/)。

## 纠正账号后的真实 SDK 联调：通过

用户确认原 RAM 账号的 Bucket 列表仅有另一个项目的 `photo-lyl`；切换账号后看到目标 `ai-notes-prod-20261014`。用户在目标 Bucket 所属账号重建 RAM 用户、创建并绑定相同受限策略，自行更新忽略的 `backend/.env`，随后明确回复“新账号授权好了”。此次重试沿用先前批准的同一测试路径与 77 字节生成图片，未新增测试文件范围。

- 配置目标检查通过。
- SDK `PutObject` 上传通过。
- `HeadObject` 大小与 77 字节一致。
- `GetObject` 返回内容与生成图逐字节一致。
- `DeleteObject` 清理请求通过。
- 删除后 `HeadObject` 返回对象不存在，当前测试对象不可读取；未删除历史版本。

当前结论：原失败由 RAM 与 Bucket 账号归属不一致导致；纠正归属并替换凭据后，受限存储访问可用。以上只证明真实 SDK 存储操作；真实 Worker 验图/派生图、网页登录上传/代理读取、ECS 部署与完整 M1 仍待验收。本次未改 Bucket 公共访问设置，未执行 commit、push 或服务器部署，未显示或保存真实凭据到证据。

## 持久化本地开发环境：已启动

用户启动 Docker Desktop 后，检查发现原容器为临时 `notes_test`。新增 `compose.dev.yaml`，创建 `ai-notes-dev-db-1`，仅绑定回环地址 55433；`notes_dev` 使用命名卷 `ai-notes-dev_dev_pgdata`。随机数据库密码只写入忽略的 `.local/postgres.env` 与 `backend/.env` 中的数据库配置，没有打印；后端原有 OSS 配置保留。

开发库 `alembic upgrade head` 与 `alembic check` 通过。API、Worker 与 Vite 已启动；API 就绪检查 `status=ok`，网页 HTTP 200，数据库容器健康，卷挂载类型为 volume。初始开发库用户与任务数量均为 0；没有写入默认账户或 QA 密码，管理员由用户通过 CLI 自行输入密码创建。

同轮在独立临时 `notes_test` 复验 68 项后端测试通过，1 项既有 Starlette 弃用提示。回归测试固定 `STORAGE_BACKEND=local`，没有发出真实 OSS 请求。网页的真实云端上传、Worker 图片处理与代理读取尚未验收；后续自动测试须明确小规模测试范围。

## 用户批准的网页与真实 OSS 图片链路：通过

用户在本机自行创建管理员并登录，随后明确回复“同意网页联调”。测试笔记为 `OSS网页联调测试`；使用两张 320×480 的生成 PNG，大小分别为 7174 与 7469 字节，合计 14643 字节。上传前预览与截图已给用户审查，未使用用户私人图片。

- 浏览器按调整后的预览顺序上传，两项均显示上传完成。
- 两个 `image.process` 任务均 succeeded，attempts 均为 1；两个 File 均 ready，各有 reading 与 thumbnail 两个变体。
- Worker 记录的原图 SHA256 与本地两张测试图一致；运行配置确认为 OSS 和用户批准的 Bucket。
- 数据库引用 2 张原图与 4 张派生图，共 6 个对象，记录合计 55021 字节。按批准范围保留在笔记中。
- 两页缩略图与阅读图可见，浏览器图片地址为项目 `/api/v1/files/.../content`，输出证据前已去除短期凭证参数。
- 放大按钮显示 125%；上传后的页序调整成功，revision 从 3 增至 4，最终顺序为 `oss-page-1.png`、`oss-page-2.png`。
- 刷新后笔记、两个页面、页序及 revision 保留；两页仍可通过缩略图切换阅读。
- 当前默认浏览器视口宽 505px，文档 scrollWidth 490px，未出现页面横向溢出；捕获的控制台 error 数为 0。

上传前与完成截图保存在本轮工作区外 visualization 目录：`oss-web-test-prepared.jpg`、`oss-web-verified.jpg`。浏览器停留在保留的测试笔记中，API、Worker、Vite 和持久开发库继续运行。

本次不声称真实手机双指触控、鼠标拖动、云端跨用户实机、进程/容器重启、备份恢复、ECS 或完整 M1 已验收。未执行 commit、push 或服务器部署。当前仍有部分写入后崩溃的孤儿变体清理任务待实现。

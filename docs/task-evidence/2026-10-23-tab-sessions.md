# 标签页独立会话验收

日期：2026-10-23（Asia/Shanghai）。本记录范围：本地源码、隔离 PostgreSQL、浏览器验证；以下结果在提交、推送及公网部署之前取得。

## 实现

新增无 Cookie 登录/注册/重置接口和 Authorization 认证，复用数据库会话、权限与撤销规则；前端 sessionStorage 按标签页保存凭证，刷新恢复，其他文档导航先清本地副本。私有图片改为认证 fetch/Blob，上传、缓存与退出联动。无 schema 迁移。

保留原有 UUID/图片预览/CI/协作交接修改；执行期间出现的 styles.css 修改来自本任务之外，未覆盖。

## 自动回归

| 检查 | 结果 |
|---|---|
| 新后端用例修改前 | 10 failed：新增路由404，错误Authorization仍回退Cookie |
| 最终后端全量 | 104 passed；包含10项新用例 |
| Alembic upgrade/check | upgrade成功，check无新操作 |
| Node 前端回归 | 14 passed；包含快照竞态、晚退出、上传401、300ms延迟回复及导航策略 |
| 配置脚本 unittest | 6 passed |
| TypeScript / Vite build | 通过；原富文本块仍有大于500kB体积提示 |
| OpenAPI / TypeScript | 已同步生成；后端契约回归通过 |
| 独立只读审查 | 首轮发现3项竞态并修复，复核无新增阻断项；独立Node14/14通过 |

后端环境为 `backend/.venv`；测试库是 `compose.test.yaml` 的 `notes_test`。浏览器使用另建的 `notes_tab_qa_20261023_68a4`，仅合成账号和本地PNG，不读写业务库、不请求真实OSS/模型。

## 浏览器验收

Browser plugin not available；沿用本机已缓存 Playwright，不增加项目依赖。目标 `http://localhost:5173`，API `127.0.0.1:8000`，独立Worker；桌面1440×1000、手机390×844。

| 流程 | 结果 |
|---|---|
| 同一 browser context 两个新page同时登录不同账号 | 通过；没有notes_session/notes_csrf Cookie |
| 两页刷新保持各自身份 | 通过 |
| 新page及window.open复制存储 | 新页要求登录，原页不受影响 |
| Chromium原生chrome.tabs.duplicate | 实测navigation=back_forward；复制页凭证清除；另账号登录/退出后原页正常 |
| 两页分别通过UI创建私人笔记 | 通过；双向跨用户读取404 |
| UI上传本地PNG→Worker处理→认证Blob图片阅读 | 通过；管理员读取成员图片404 |
| A退出后B刷新及读取私人笔记 | 通过 |
| 两页同账号分别登录，仅退出其中一页 | 另一页保持登录 |
| 退出服务端已提交但响应延迟，401后重新登录，再释放旧退出响应 | 新会话保持，另一页不受影响 |
| 注入旧共享Cookie | 新页仍需独立登录，现有页身份不变 |
| 桌面/手机页面、有效内容、框架错误浮层及控制台 | 通过；无意外运行错误或手机横向溢出 |

证据根目录：`C:/Users/32159/.codex/visualizations/2026/10/23/01a16830-35ba-7e22-b3e5-be7731562d03/`。

- `tab-sessions-qa.mjs`：真实多标签页端到端及延迟响应脚本。
- `native-duplicate-qa.mjs`、`duplicate-test-extension/`：仅临时独立浏览器配置中加载的tabs测试扩展，用官方chrome.tabs.duplicate触发复制；不访问用户现有浏览器配置。
- `tab-admin-desktop.png`、`tab-member-image-desktop.png`、`tab-mobile.png`：合成账号截图。

## 未验证

没有部署公网或在服务器更新镜像；未在真实手机、Firefox或Safari实测，也未执行真实OSS上传、模型调用或生产恢复演练。截图与本地通过不替代这些验收。

# 标签页独立登录设计

用户已确认同一浏览器的不同标签页分别登录不同账号，互不影响；采用前一轮说明的 sessionStorage + Authorization 方案。

- Cookie 会话继续兼容旧客户端；新增 `/auth/tab/login`、`/auth/tab/register` 返回 user、session_token 和 csrf_token，不写登录 Cookie。
- 前端只读取本标签页 sessionStorage 的凭证，不继承共享 Cookie 身份；仅 navigation.type=reload 恢复登录，navigate/back_forward/未知类型先清本地副本。新标签页、复制标签页、从地址栏重新进入和页面前进后退需登录；应用内导航为同一文档，不受影响。
- 原 AuthSession 表、有效期、哈希存储、停用与密码重置撤销规则保持；无 schema 迁移。Authorization 出现时严格优先，失败不得退回 Cookie。
- 保留 Origin、认证后每会话 CSRF 和限流。tab 登录/注册/密码重置采用 Origin + 必须的 X-Notes-Client 自定义请求头防护，不读写 Cookie，防止新标签页同时登录时共享 CSRF Cookie 竞争；旧接口继续原双提交验证。退出仅撤销请求使用的会话，不删除其他会话 Cookie。
- 私有图片以带 Authorization 的 fetch 获取 Blob，再创建 object URL；卸载/重载释放 URL 和中止下载。上传请求同步携带当前页凭证。
- 登录/退出/401 清理当前页查询缓存；草稿继续按 owner 分开保存，退出不清除其他页可能使用的草稿。
- 复制标签页或 opener 复制 sessionStorage 时先由导航策略清副本。reload 的补充冲突检测使用 BroadcastChannel，只交换随机 scope/文档标识；较早持有者回复即使超过初始150ms也会清空新页副本、触发缓存清理，不撤销原会话，不广播凭证。
- 不支持多个浏览器标签页作为相互不信任的安全边界：同源 XSS 可以读取 sessionStorage。现有富文本安全处理保持，凭证禁止放入 URL、localStorage、日志或广播消息。
- 替代方案：浏览器配置隔离无需代码但不满足普通标签页要求；每页独立 HttpOnly Cookie + 选择器需另设计多 Cookie 生命周期。本次采用用户已认可的显式请求凭证。

验收：同 browser context 两页不同账号、刷新、退出、重新登录、复制页、旧 Cookie 干扰、注册、CSRF、过期/停用/重置、笔记读写与跨用户 404、图片上传和阅读、前端构建/类型契约/后端回归。

实现阶段只修改和验证本地，保留原有未提交修改。2026-10-23 用户随后明确授权将标签页会话、HTTP UUID、侧边栏及文档改动一起提交推送，并由 Codex 通过现有 SSH 更新公网；部署须先备份并保留旧版本回退。

参考：[sessionStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/sessionStorage)、[BroadcastChannel](https://developer.mozilla.org/en-US/docs/Web/API/BroadcastChannel)、[Authorization](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Authorization)、[OWASP 自定义请求头防 CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#employing-custom-request-headers-for-ajaxapi)。

# T23/T24 提醒、统计、日历与真实活动前端证据

日期：2026-11-01。仅本地合成账号和拦截 API，无数据库操作、真实模型、付费请求、服务器登录、部署或 Git 提交。保留已有 dirty/untracked worktree，未修改 `App.tsx`、测验组件、生成类型或后端文件；最终 App 导航/provider/通知跳转由主任务整合。

## 用户可见行为

- `LearningPreferences.tsx` 使用 GET/PATCH `/learning/settings`，保存服务端 `revision`、IANA 时区、当地 HH:MM 与站内提醒开关。读取错误可重试；409、网络不确定、408/5xx 保留输入并阻止盲目再次保存，必须显式读取成功后采用服务器版本。后台刷新不覆盖草稿。
- 提醒历史以50条分页，展示生成时的当地日期、原时区、笔记/卡片/测验数量和当地生成时间；明确只生成站内通知。前端没有伪造通知的 timer 或通知生成调用。
- `LearningOverview.tsx` 按已保存用户时区默认最近30个当地日；显式日期含首尾最多93天；服务端422扫描限制明确提示缩短范围。统计与日历并行查询；日历支持 note/card/quiz 筛选以及未来30天快捷范围。
- 分别展示有效总时长、reading/review/card practice/quiz 分类、正式复习次数、临时练习、每天去重复习对象数、Again/Hard/Good/Easy 主动评价分布、测验交卷/已判分/待复核/实际得分/满分/人工修正。每日明细31天分页。每天对象数不相加为跨天唯一对象数；总时长合并重叠，分类可重叠不能相加。阅读不等于掌握，不展示精准掌握概率。
- 日历显示历史正式完成、临时练习、测验交卷以及当前有效未来到期计划，日期说明用户当地时区。`ReviewCenter` 默认读取同一保存设置，未读取成功前显示加载/错误而不伪装为设备时区；其现有手动 `datetime-local` 输入仍明确标注设备时区并转换 UTC。

## 活动与隔离实现

`ActivityTracker.tsx` 导出 `LearningActivityProvider({owner,sessionId,children})` 与 `useLearningActivity(kind, resource|null)`；`activity.ts` 提供可冻结时钟验证的纯 clock 和 queue。

- 真正接入 NotesWorkspace 当前有效笔记、CardReader 已确认卡片、ReviewNote/ReviewCard；QuizAttempt 的 quiz hook 由测验前端任务接入。无当前有效资源就传 null。后端还会复核当前账号及卡组/资源有效性。
- 页面可见且真实可信 pointer/key/touch/wheel 操作后开始（异步打开资源会消费一次性短期打开手势），每15秒切片；60秒没有操作截断闲置，隐藏立即 flush 并停止，恢复需要新操作；切资源 flush 旧段，新资源重新激活。程序派发事件不激活；DOM程序滚动事件本身不激活；真实滚动通过 wheel/touch/key/pointer 手势记录。pagehide 保存末段。每个片段 UUID 稳定，严格正时长、最多60秒，时间发送 UTC。
- 短暂网络/5xx/408/429 以原 UUID 和完整内容退避重试，上限6次，达到上限的段仍仅在24小时窗口内保留；400/403/404/409/422 等确定错误丢弃该批并停止对应资源，避免无限错误重试。
- 队列最多500段，单批最多100，sessionStorage 内按 owner+本标签页随机 session scope 隔离；同一会话刷新可恢复24小时内段。新账号/新会话不继承旧队列，退出立即清理。请求沿用已有 API 的会话世代/取消屏障；迟到响应不能恢复队列或写入新账号状态。存储不可用时仅保留有界内存队列。

## 验证结果

Browser plugin not available：本会话未列出 Browser plugin/browser skill，使用已缓存 Playwright 与已安装 Edge，无新增依赖。执行位置为项目根目录；自有 Vite `http://127.0.0.1:5182`，结束后停止。

| 范围 | 证据与结果 |
|---|---|
| 纯 helper 与全前端回归 | `node --experimental-strip-types --test tests/*.test.mjs`：144 passed，其中新增 learning 18项；日志 `.local/qa-learning-stats-20261101/tests.log` |
| 类型检查与生产构建 | `npm run build`：通过；日志 `.local/qa-learning-stats-20261101/build.log` |
| 深入真实组件 render harness | `node .local/qa-learning-stats-20261101/learning-qa.mjs`：37项通过；真实 React 组件/provider + 合成 HTTP，不代替最终 App 集成验证 |
| 最终真实 App | `node .local/qa-learning-stats-20261101/app-qa.mjs`：13项通过；真实登录→学习通知→复习中心→原笔记→卡片→提醒→统计→退出 |
| 桌面/移动 | 1366×1000、390×844；页面身份正确、非空、无框架错误 overlay、无未解释应用 console/page errors；移动 document 无横向溢出，截图人工检查通过 |

深入组件覆盖：设置/历史读取失败重试、409和500保留输入、读取不覆盖/明确采用、revision PATCH、历史分页50、保存时区与当地日期默认值、union 900秒与分类各600秒不能相加的说明、正式/临时/去重对象/评级/测验值、日期93天、日历kind实际查询、422扫描受限友好提示；15秒心跳、加载/伪造手势不激活、60秒idle停止、切资源/hidden flush、恢复不自动继续、同ID重试、退出换账号，以及**人为持有旧事件响应→退出→重新登录→释放迟到响应**不恢复旧队列。

实际 App 覆盖：可信 `target=reviews` 站内通知打开复习中心；用户 America/New_York 与设备 Asia/Shanghai 不同时仍按保存时区展示；从 ReviewNote/NotesWorkspace/CardReader 各自产生真实资源 UUID 的 review/reading/card_practice POST；最终新增导航可达；提醒/统计390px布局；退出卸载 provider、清队列且后续无活动请求。Quiz 作答hook由测验任务另做作答流验收。

预期 console 网络状态包含合成409/500/422；Edge 本地网络访问限制导致 Vite HMR WebSocket 连接警告，静态模块、API 拦截与交互正常。结果 JSON 的 `errors` 均为空；没有把这些开发连接警告当成产品异常。初始 harness 迭代修正了 Vite preamble/模块实例设置与虚拟时钟网络任务的步进方式，最终结果来自完整重跑。

## 证据位置与边界

本地目录 `.local/qa-learning-stats-20261101/`：`results.json`、`app-results.json`、两个可复跑 QA 脚本、tests/build 日志及桌面/移动截图。`app-stats-mobile.png`、`app-preferences-mobile.png` 为最终实际 App；不把 harness 导航按钮称为产品导航。

本次无后端真实 PostgreSQL 集成、真实长时间离线设备/多设备浏览器验证、手机真机或公网验收。后端 union/DST/账号边界见独立后端证据；前端使用合成服务器结果验证展示及真实请求契约。未修改总任务完成清单，最终独立审查和完整 M2 验收由主任务继续。

## 独立规格审查 P2 修复：异步打开后静读漏记

独立规格审查指出首轮 QA 点击列表后又额外点击正文，遗漏了“打开对象后静读”的真实路径：打开时的 pointerdown 早于异步 detail/hook 注册，原实现把该手势丢弃。已修复并更新上述最终结果。

- 仅可信 pointerdown/keydown/touchstart 生成一次性打开 intent，5秒后失效；下一资源注册消费后立即清除，不能复用旧60秒任意手势反复激活程序切页。pointermove/wheel 只延续当前资源活动，不生成跨资源 intent。
- 注册成功才开始片段，异步加载耗时不计入学习；原手势的60秒 idle 截止时间不因加载完成延后。加载超过5秒需新的真实操作。
- hidden/pagehide/stop 清除未消费 intent；恢复可见和无手势自动加载均不激活。忽略 DOM scroll 事件，避免 focus/scrollIntoView 自动滚动伪造操作；真实滚动由 wheel/touch/key/pointer 手势观察。
- StrictMode 对同一资源的即时 effect 清理/重挂载，允许100ms内恢复原激活，保持原idle截止；该恢复仅绑定同一 kind/resource，不能转移到别的资源。
- 新增4项 pure clock 回归：异步打开、过期/一次性消费、hidden清除、StrictMode绑定同资源。Provider 浏览器 harness 在 React.StrictMode 下验证“真实点击异步打开→等待加载→静读30秒、不额外点击正文”产生正确资源片段，并验证 intent 尚未消费时隐藏会清除它、恢复后无事件。
- 实际 App QA 的 note/card GET 刻意延迟400ms，复习列表、打开原笔记、卡片列表三条路径均只点打开控件；等待真实正文到达后静读30秒，分别收到 review/reading/card_practice 的正确资源 UUID。取消了旧 QA 中的正文额外 click，结果 JSON 的检查名称明确区分新路径。

最终全前端142项测试、生产build、33项Provider/组件浏览器检查及11项实际App检查均通过。独立复审由主任务审查 Agent 执行，不将本执行者的自检称为独立复审。

## 独立质量审查 P2 修复：保存等待期输入覆盖

质量审查指出偏好 PATCH 在途时仍允许编辑控件，成功响应 `setDraft(saved)` 会覆盖等待期间的新输入。已采用最小交互锁：保存期间禁用时区、当地时间、提醒开关三个编辑控件，同时禁用保存/版本采用操作，并显示“正在保存学习偏好，编辑暂时暂停；完成后可继续修改。”完成或失败后解除编辑锁；原有失败保留输入/显式核对流程保持。

真实 React/Provider 浏览器 QA 持有 PATCH 响应：核对三个控件均 disabled，正常 Playwright `fill('UTC')` 被阻止且原 `Asia/Shanghai` 输入不变；释放响应后控件可编辑、显示与提交值一致。该路径新增3项检查，组件/Provider浏览器总33项通过；全前端142项测试与生产build再次通过。最终实际App11项结果来自上一轮完整导航验收，本次未改App或导航接口。

## 6.5 补充：本期阅读覆盖与历史阅读事实

使用后端已稳定、主任务已重新生成的真实 API 类型：`StatsOut.reading_progress` 的 `recorded_notes/active_notes_read/active_notes_total`，以及每日 `StatsDay.reading_notes`。未手写替代 API 类型，未修改 App、ActivityTracker 或测验组件。

- 统计页面新增独立“本期阅读覆盖”：展示“当前可读笔记覆盖 1 / 3 篇”和“本期有阅读记录的笔记 2 篇”。分子、分母只含当前未回收且可读笔记，期间阅读历史按笔记跨天/跨设备去重；之后回收的历史记录仍计入期间记录，不计当前可读覆盖。
- 每日明细新增当天去重阅读笔记数，并说明每日阅读笔记/复习对象数不可跨天相加成期间唯一总数。仅 review 活动不算 reading，有阅读活动不代表已读完或掌握；不展示掌握率或完成率。
- 旧 mock/缺字段显示“未提供阅读进度”或“未提供当天阅读进度”，不默认为真实零。收到明确零值时显示 0 / 3 篇与0篇记录，仍保留事实口径说明，不称为已读完/学习已完成。畸形/缺子字段也不补造数据。
- 新增2项 helper 回归，覆盖 recorded2/current1/total3、明确零、历史记录保留且当前总数零，以及旧/畸形 DTO 的 unavailable。
- 两套合成 HTTP mock 均补齐真实新字段。组件/Provider QA 新增阅读覆盖/日数、明确零、旧响应缺字段、390宽度检查；实际 App 新增本期数值/口径和每日阅读数检查。截图 `reading-coverage-mobile.png`、`app-reading-coverage-mobile.png` 已人工检查，390px没有横向溢出。

最新完整重跑：全前端 **144 passed（learning 18项）**，`npm run build` 通过，组件/Provider **37项**、最终实际 App **13项** 全通过，两个结果 JSON 的 `errors` 均为空。日志和结果仍在同一 `.local/qa-learning-stats-20261101/` 目录。仅合成 API，未操作数据库或公网。

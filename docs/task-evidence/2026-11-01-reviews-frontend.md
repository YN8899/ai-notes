# T20/T22 复习中心前端实现与验证

日期：2026-11-01。本记录只描述当前前端与合成接口验证，不代替真实 PostgreSQL 并发证据或用户验收。

## 已实现

- `App.tsx` 增加懒加载复习中心入口；笔记详情通过 `ReviewJoin` 为实际已选对象建立独立计划，已存在计划的 409 提供复习中心入口。卡片确认说明自身计划与暂停恢复规则，不额外提交建计划请求。
- `ReviewCenter` 支持服务器到期待办/全部计划、类型/状态/有效对象/日期筛选、最多 50 条分页。笔记读取当前正文并可打开原文；卡片复用真实翻面与强来源核验，来源失效仍保留卡片且不请求旧图片；测验显示摘要，提供可选 `onOpenQuiz` 回调，未集成时不渲染无效按钮。
- 正式复习与临时练习明确区分，四档主动评价；manual 正式评价须填写未来时间与原因，FSRS 不传手动时间，也不把测验分数映射为记忆评价。练习成功明确告知不改变排期、版本和算法状态。
- 明确暂停/恢复/归档、改期、策略切换，以及恢复上次 FSRS /重新开始选择。历史显示来源对象、实际时间、操作/评价、旧新排期、原因；不显示原始算法 JSON 或无依据掌握率。
- 写入前保存固定完整请求、UUID 和原始 revision。网络未知/408/5xx 保留原请求，支持显式同编号重试与只读日志对账。刷新/聚焦/查询不自动写入。409 保留输入；后台读取不会自动采用新版本。当前可复习资格变化会立即隐藏阅读对象并禁用评价，而不偷偷重置草稿版本。
- 缓存按 owner、当前 tab-session 标识隔离；待核对写入按 owner 和非凭据 scope 存于当前标签页。离开复习页面后再退出/切换账号也清理待核对请求，迟到结果不显示。
- 列表/历史默认显示 Asia/Shanghai；`ReviewCenter.timeZone` 可接 T23 IANA 偏好。所有 `datetime-local` 明确标注设备时区，以 `new Date(value).toISOString()` 转 UTC。

## 验证结果

| 验证 | 结果 | 证据 |
|---|---|---|
| `node --experimental-strip-types --test tests/*.test.mjs`（frontend） | 116/116 PASS，其中新增 reviews 10 项 | 时间转换含中国/纽约设备时区、四档 payload、练习只记日志、服务器筛选、未知响应分类、请求保存与历史文案 |
| `npm run build`（frontend） | PASS | TypeScript 与 Vite 构建通过；T21 重生成后修正 cards.ts 的 5 个 Generation schema alias 为实际 card_schemas 命名空间 |
| 实际 App + Edge/Playwright，1366×1000 与 390×844 | 50 checks PASS，0 runtime/framework errors | `.local/qa-reviews-20261101/results.json` 与 `reviews-qa.mjs` |
| 桌面/手机视觉检查 | PASS，无文档横向溢出 | 同目录 desktop-list/history/fsrs/card/gone-source/image、mobile-list/card 截图 |
| 自有 Vite 5180 | 已停止 | 只终止本次启动的进程，未操作其他端口 |

浏览器能力：Browser plugin not available，使用已缓存 Playwright 与已安装 Edge，无新增依赖。所有 `/api/v1/**` 请求由本地测试脚本拦截，不连接真实数据库、OSS 或模型，不使用真实凭据和付费 API。7 条预期资源错误来自合成 500/409/404 场景，不是未解释的应用运行时错误。

50 项包括：manual/FSRS 各四档、练习排期完全不变、暂停/恢复/归档、改期与策略历史、restart/resume_last、500 已保存后原编号重试只推进一次、刷新不自动 POST、只读对账、409 草稿/版本保留、来源消失/图片遮挡/翻面、资格变更、三类筛选/UTC日期/分页、移动端、笔记已有计划与成功新建后直接打开、离开页面后的账号隔离。

## 集成接口与边界

`ReviewCenter` props：`owner`, `onOpenNote(id)`, `onOpenQuiz?(id)`, `initialPlanId?`, `timeZone?`。

`ReviewJoin` props：`owner`, `kind: 'note' | 'quiz'`, `id`, `onGoReview?(planId?)`。测验 UI 完成后可复用于已确认测验的显式加入复习入口。

此轮未验证真实测验题目作答 UI、真实后端 FSRS 算法与并发（见独立后端证据）、真实通知偏好（T23）或用户 acceptance。没有提交 Git、修改生成契约或调用真实外部服务。

## 独立审查后修复：跨对象待核对请求归属

独立 spec 审查发现 P2：A 笔记的建计划请求已保存但响应 500，切换到 B 笔记后，B 会恢复同一 owner/scope 下 A 的待核对请求。核对或同编号重试得到 A 计划时，原先无条件 `setPlan(receipt.plan)` 错误使 B 显示已加入并导航 A。

最小修复：`matchingReviewPlan` 同时比较 `plan.item.kind` 和对应的实际 `note_id/card_id/quiz_id`。`ReviewJoin` 仅认领匹配当前对象的计划；旧请求正常对账并解除阻塞，不认领其他对象计划。保留原 owner/scope 待核对存储、原编号、完整参数与只读核对语义，没有丢弃请求或自动生成新编号。

- RED：`.local/qa-reviews-20261101/join-switch-red.log`，实际 App 中 A 已保存后 500 → 打开 B → 只读核对；B 的“加入复习”按钮错误消失，用例失败。
- GREEN：`join-switch-qa.mjs` / `join-switch-green.log` / `join-switch-results.json`，15 checks PASS。分别覆盖只读对账和原编号重试；B 恢复待核对时不写入，处理 A 后保持未加入、按钮可用、不会导航 A；B 能使用新编号建立自己的独立计划并直接打开 B。原请求只创建 A 一次，重试完整参数与编号保持一致。无应用运行时错误。
- 新增纯 helper 回归：三类对象的同类型/同 ID 匹配、跨 ID、同 ID 跨类型、空回执。
- 全前端 117/117 PASS，`npm run build` PASS。仅操作前端、合成接口与本地证据；未改 generated types、未提交。自有 Vite 5180 已停止。

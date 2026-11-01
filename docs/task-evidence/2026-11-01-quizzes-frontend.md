# T21 测验前端执行证据

- 日期：2026-11-01；设计依据：`docs/superpowers/specs/2026-11-01-quizzes-design.md`。
- 原工作区已有大量 tracked / untracked 改动，全部保留；仅实现授权 T21 UI 与少量 App 测验入口，不 commit / push / 部署。
- 使用 `frontend-testing-debugging` 与 `react-best-practices` 技能，沿用既有 React、TanStack Query、`api`、`authSession`、`NoteError`、私人来源选择与复习计划。
- Browser plugin not available：会话没有 Browser plugin / browser skill，因此使用机器已有缓存 Playwright + Edge headless，无新增依赖。真实 Vite App + 拦截合成 API；没有连接数据库、真实账号、模型服务或其他收费 API。
- 本文证明前端实现及合成浏览器流程，不替代真实后端 / 数据保护 / 整体 M2 独立验收。后端证据另见 `2026-11-01-quizzes-backend.md`。

## 实现范围

1. `QuizWorkspace.tsx`：真实导航、状态筛选、每页 50 份测验和 50 条作答历史；手工新建 / 编辑 / 确认 / 归档 / 恢复；所有版本写入携带页面所见 revision。确认测验通过 `ReviewJoin` 显式加入独立计划，`ReviewCenter.onOpenQuiz` 打开真实测验工作区。
2. `QuizEditor.tsx`、`quizzes.ts`：五题型单选、多选、判断、`{{blank}}` 填空和简答；2–8 个合法选项、正确答案、1–100 整数分值、解析 / 评分标准、可选私人来源；1–50 题全部校验后原子保存。来源保留原版本或显式替换 / 移除；题目用稳定本地身份，移除中间题不会把来源编辑状态串到邻题。作者 409 保留输入和原版本，不自动重定版本。
3. `QuizAI.tsx`：文字选段 / 普通图片 / 单页 PDF，混合题型 1–20 题且数量不少于所选题型数，支持现有两种模型；先预览真实范围、模型和一次调用，再勾选明确外发确认。参数变化必须重新预览。生成结果只作为待人工审核草稿，显示状态 / 错误 / 用量。简答评分预览明确显示 question / reference_answer / rubric / max_points / student_answer；不外发来源原文件。
4. `QuizAttempt.tsx`：开始携带 quiz revision 与 request_key，随后以 GET 获取当前作答，原开始请求收据和缓存 DTO 都不冒充当前状态。作答只读取脱敏 Attempt DTO，不从作者缓存获取正确答案 / 解析。五类型控件；500 ms debounce，全作答串行保存并使用最新 attempt revision，迟到保存回复不覆盖较新输入。
5. `QuizMutation.tsx`：开始 / 保存 / 交卷 / 人工复核 / AI 操作在发送前保存完整原编号和参数。网络失败、500、409 保留请求和输入；刷新 / 恢复网络 / 重新打开均不自动 POST。显式重试只发同编号与原参数。409 先只读核对，再明确结束本机待处理状态并继续保存；不会悄悄改变旧请求参数。AI 不确定结果可只读核对或明确同编号重试，不自动新建收费请求。
6. 未保存输入按 owner + 标签页 session scope + attempt 隔离并保留。切换对象、卸载、退出、账号切换均有活性栅栏；全局退出清理在工作区已经卸载时仍执行，旧 pending / 草稿不会残留到新账号。开始迟到回复不会关闭新打开的编辑草稿或切走既有作答。
7. 交卷需要显式勾选，所有答案保存完成后才能提交。展示得分 / 满分 / 待复核、本地判分规则（多选集合完全相等、填空 trim / casefold 后全空全对才得分）；所有题目可人工评分并说明原因和反馈，重算总分。不可变历史通过 `reviews[].answer_id → answers[].id → question_id` 定位，显示中文评分来源与前后分数。
8. `QuizSource.tsx`：题目和历史 Attempt 使用各自 source GET 重新核验；成功读取后才展示私人文字 / 图片；410 / 失败立即卸载旧图，保留用户题目与答案，不读取旧文件或拿其他当前页冒充。
9. `QuizAttempt` 挂接 `useLearningActivity('quiz', {quiz_id})`；仅在当前作答成功读取后注册，具体可见性、用户活动、idle / heartbeat / 上报身份由现有 `LearningActivityProvider` 统一处理。

## 验证与结果

实际流程：App 登录 → 学习测验 → 五题制作 / 来源 → 原子保存 → 刷新 → 审核确认 → 开始 → 自动保存 / 断线 / 版本冲突 → 正式交卷 → AI 建议 / 人工复核 → 独立复习计划 → AI 混合草稿 / 页面来源 → 桌面与手机 → 迟到响应及账号切换。

| 验证项 | 结果 |
| --- | --- |
| 页面身份、非空、无 Vite overlay、真实按钮交互 | PASS |
| 五题型制作 / 正确答案 / 文本来源 / 保存刷新 / 作者 409 | PASS |
| confirmed / draft / archived 保护、改题不改旧 Attempt snapshot | PASS |
| limit=50 / offset=50 测验分页 | PASS |
| in_progress 不显示作者参考答案与解析 | PASS |
| 500 已保存未知结果，同 key 和完整原参数重放 | PASS |
| 409 保留输入、只读核对、明确解除后继续保存 | PASS |
| 同页多题快速输入串行保存与 attempt revision 前进 | PASS |
| 离线失败草稿、刷新恢复不自动 POST、同 key 明确重放 | PASS |
| 未保存答案不能交卷、确定题评分与简答 needs_review | PASS |
| 简答 AI 预览失败不调用、五字段外发预览、未知同 key、用量 | PASS |
| 双简答 Q1 unknown → 关 Q1 开 Q2，只读核对保留 Q1 身份 | PASS |
| Q2 重试只发 Q1 原 key / 参数，Q1 分数与反馈不归到 Q2 | PASS |
| 显式回到原第 1 题后才展示 Q1 已恢复建议 | PASS |
| 人工复核重算 5/5、answer_id 历史正确指向第五题 | PASS |
| 生成预览错误不调用、混合题型、unknown 不自动新 key、GET 核对 | PASS |
| 图片 / PDF 授权单页预览，无额外生成调用 | PASS |
| 来源 410 不读旧图且答案保留 | PASS |
| ReviewJoin 独立计划与真实 ReviewCenter → QuizWorkspace | PASS |
| 原开始旧收据重放后 fresh GET 保留后来保存的新答案 | PASS |
| A 归档 PATCH 迟到，切到 B 后不混用 A 内容 / B 作答列表 | PASS |
| 显式 GET 冻结 draft rev1，确认 PATCH 先到 rev2，旧 GET 最后到不降级 | PASS |
| A 显式读取迟到，切 B 后不覆盖 B 当前对象 | PASS |
| 外部标签页交卷服务器 A，本地离线 B 仅作未提交草稿保留 | PASS |
| 开始迟到响应不覆盖新编辑草稿，跨账号迟到数据隔离 | PASS |
| 登出清除 pending / 草稿（包括先前卸载的工作区） | PASS |
| 1366×1000 / 390×844；手机列表与 AI 区域无横向溢出 | PASS |
| 最终合成浏览器断言 | **46 / 46 PASS** |
| 应用 runtime / 相关 console error、未拦截 API | **0 / 0** |
| 新增 helper 测试 | **9 / 9 PASS** |
| 全部 frontend helper 测试 | **142 / 142 PASS** |
| TypeScript + production build | **PASS** |

`package.json` 没有 `npm test` script；直接运行项目现有 `node:test` 文件。源码未新增依赖。

执行位置与命令：

```powershell
# D:\codex-pj\ai-notes\frontend，独立临时开发端口，测试结束后停止
npm run dev -- --host 127.0.0.1 --port 5181 --strictPort

# D:\codex-pj\ai-notes，真实 App 的合成 API 浏览器回归
node .local/qa-quizzes-20261101/quizzes-qa.mjs

# D:\codex-pj\ai-notes\frontend，边界 / 身份 / 来源 / 全量已有 helper 回归
node --experimental-strip-types --test tests/*.test.mjs
npm run build
```

## 本地证据与限制

- `.local/qa-quizzes-20261101/quizzes-qa.mjs`：完整可复跑合成浏览器脚本，敏感登录字段不记录到请求日志。
- `.local/qa-quizzes-20261101/results.json`：最终 46 条断言、合成请求、零应用错误和零未模拟 API。
- `.local/qa-quizzes-20261101/helpers.log`、`build.log`：最终 142 项测试与 build 输出。
- 截图：`desktop-gone-source.png`、`desktop-scoring-history.png`、`desktop-ai-generation.png`、`mobile-list.png`、`mobile-ai.png`、`account-change.png`、`external-submit-local-draft.png`、`two-short-answer-recovery.png`；均为合成资料。
- 期间一次运行因 Windows / Edge 网络挂起出现 `ERR_NETWORK_IO_SUSPENDED`、时间跳变与定位超时；机器恢复后完整重新运行通过。另保留开发期失败 artifact 供追溯，最终判定以 `results.json` 为准。
- 没有宣称真实后端五题判分、AI 供应商、外部计费、真实图片/PDF转换或公网部署在本次前端测试中通过；这些由后端独立测试和后续整体验收提供证据。
- `AttemptList` 不包含 request_key，开始请求未知时读取原测验作答列表供用户明确选择，不能把列表第一条误认成原请求；精确恢复仍可明确重试相同原始请求，随后 GET 当前状态。
- 私人来源图像的选区编辑复用已有 `CardSourcePicker` / `RegionCanvas`；本次验证了文字、图片、PDF 页面范围与来源失效，不重复宣称所有图像裁剪几何场景已新做实机验收。

## 独立复验入口与 props

- 主要可审查文件：`frontend/src/features/learning/QuizWorkspace.tsx`、`QuizEditor.tsx`、`QuizAttempt.tsx`、`QuizMutation.tsx`、`QuizAI.tsx`、`QuizSource.tsx`、`quizzes.ts`、`quizzes.css`、`frontend/tests/quizzes.test.mjs`。
- `QuizWorkspace`: `{owner, initialQuizId?, initialAttemptId?, onOpenNote(id), onGoReview?(planId?)}`；`owner` 和活动 session 必须匹配，内部按 owner + session id 重建工作区。
- `ReviewCenter.onOpenQuiz(id)` 已通过 App 导航送入 `initialQuizId`；确认测验通过 `ReviewJoin` 使用实际已拥有测验 ID，无 UUID 文本粘贴入口。
- `QuizAttempt`: `{owner, sessionId, id, onClose, onOpenNote}`；只从 `/learning/quiz-attempts/{id}` 及其同域操作获取作答内容。
- 当前 QA 端口为临时本地 5181；脚本无需 DB。全量 App 的其他 M2 导航 / provider 集成由主执行者管理。

## 独立审查修复

主执行者独立审查确认两项真实并发风险，已最小修复并新增浏览器回归：

- 确认 / 归档 PATCH 在发起时冻结 quiz ID 与 viewEpoch；A 的迟到成功或错误不能污染切换后的 B，成功写入仍可刷新缓存。
- 其他标签页交卷后，已评分区域只显示服务器已提交答案；本地离线 / 409 草稿独立标记为未提交且不计分，保留用户内容，不能冒充对应本次分数的答案。

修复后完整浏览器 46 项、全量 helper 142 项与 build 再次通过；5181 临时 Vite 服务最终已停止。

### 第二轮独立复审：显式读取竞态

复审通过实际 QueryObserver 时序确认：读取最新 GET 抓取 draft rev1 后延迟，期间确认 PATCH 返回 confirmed rev2，旧 GET 随后返回可能把当前 UI 倒退。已增加 `readLatest` 的 quiz ID / viewEpoch / 会话活性栅栏，并让读取和 PATCH 统一通过 `adoptSnapshot` 采纳同对象不低于当前 revision 的结果。旧读取不覆盖新写入，旧写入也不覆盖较新的已读取版本；切换对象或进入新编辑器后，旧读取不改变当前快照。

浏览器回归使用可控 Promise 挂起实际 App 的合成 GET（在挂起前冻结响应 rev1），先完成确认 PATCH，再释放旧 GET；确认当前仍为 confirmed rev2 且开始作答可用。第二条将 A 的读取挂起后切到 B，再释放 A，确认 B 保持不变。完整 46 项和全量 142 tests / build 已再次复验。

### 第三轮独立质量审查：双简答 AI 恢复归属

确认并修复同一 Attempt 的共享 grading 通道恢复可能误归属到另一题的 UI 问题。仍按 Attempt 持久保存完整原 pending / request_key，不改成丢失原请求的新通道，也不自动生成新编号。面板现在明确显示当前题号 / 题干；原请求恢复始终显示原题号 / 题干，当前题与原题不同则只显示原记录状态及“回到原请求第 N 题”入口，不显示原题分数或反馈。评分结果须同时匹配 attempt_id 和 question_id 才进入当前题结果区域。评分组件在同 Attempt 切换题目时保持身份，回到原题可查看已恢复结果。

实际 App 使用两道手工简答题，Q1 调用返回 500 后关闭 Q1、打开 Q2：只读核对不调用新 AI 且保留 Q1 pending；明确重试发送完全相同 Q1 request_key / 参数；Q2 结果区域没有 Q1 分数或 `Q1_ONLY_FEEDBACK`；显式回到 Q1 后才显示该反馈。新增 4 条断言，完整浏览器 46/46、全量 frontend 142/142、build 再次通过。证据截图 `two-short-answer-recovery.png` 为回到原题后的正向结果。

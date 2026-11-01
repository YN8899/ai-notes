# T20/T22 复习后端执行证据

日期：2026-11-01。仅本地隔离 PostgreSQL，未提交、推送或部署，未使用真实模型/OSS凭据。

## 实现

- `review_items` 使用 note/card/quiz 三个真实 owner 复合外键，CHECK限定唯一匹配对象；每对象独立计划。
- `learning_quizzes` 的 `QuizIdentity` 是 T21 身份基础：owner/title/status/revision/question_count/timestamps。只有 confirmed 且 question_count>0 可复习。
- `review_plans` 的 active/paused 部分唯一索引防止重复有效计划；归档后可显式新建。
- `review_logs` 保存不可修改的旧/新状态、评级、服务器时间、原到期、原因及原始请求回执。数据库 trigger 拒绝 UPDATE；合法账号数据生命周期仍可 DELETE。
- 所有写接口使用 CurrentUser 的账号/session刷新与 CSRF；service 按 owner→plan 加锁，重新读取 revision。同 request_key 重放原回执，参数不同报409；两个并发请求只有一次推进。
- 卡片确认在同事务建立独立 manual 计划；草稿/归档或卡片集归档暂停并记日志；恢复对象不自动恢复计划；编辑已确认卡片不会重建用户归档的计划。
- 临时练习只追加日志；无论是否提供计划，都不调用调度器，不改变到期/revision/状态。
- manual 评价明确指定未来下次时间及原因；FSRS 使用官方适配器，四档评级与官方输出一致。切换保留历史状态，可明确 resume_last/restart；未知算法版本拒绝切换绕过。

## 接口

API 前缀 `/api/v1/learning`，精确类型见 `backend/app/modules/learning/review_schemas.py`。

- `GET/POST /review-plans`；POST `{kind,note_id|card_id|quiz_id,request_key,strategy?,next_due_at?}`。
- `GET/PATCH /review-plans/{id}`；PATCH operation 为 pause/resume/archive/reschedule/switch，携带 request_key/revision，改期及切换必须有 reason；进入 FSRS 必须明确 fsrs_mode。
- `POST /review-plans/{id}/reviews`；request_key/revision/rating，manual 必须 next_due_at/reason。
- `POST /review-items/practice`；对象/request_key/rating，可选成对 plan_id/revision。
- `GET /review-due`：仅本人可复习、active 且已到期的计划。
- `GET /review-logs`：plan_id/item_id/action 过滤；全部列表有 offset/limit<=50/total。

所有写接口返回 `{plan,log}` 回执。GET 计划包含只读 item 摘要、title/eligible。响应不含 owner_id、存储对象键或凭据。所有时间输入要求时区，服务器存 UTC。

## 迁移

`d58a3b7c901e` parent `c4d72e9a1f03`，冻结SQL，不导入运行时ORM。新增4表，metadata共38表，加 alembic_version 共39表。回填仅 active 集合内 confirmed 卡片，生成独立 manual 计划及 create 日志。

函数 `reject_review_log_update()`；trigger `review_logs_immutable BEFORE UPDATE ON review_logs FOR EACH ROW EXECUTE FUNCTION reject_review_log_update()`。整站备份函数/trigger白名单由主执行者同步处理。

## 验证

执行位置 `D:\codex-pj\ai-notes\backend`，使用 `.venv\Scripts\python.exe`，`TEST_DATABASE_URL=postgresql+psycopg://notes:notes@localhost:55432/notes_test`（仅一次性本地测试账户）。

- RED：新测试在旧 c4 schema 报 `learning_quizzes does not exist`，升级 d58 后推进。
- `python -m pytest tests/test_reviews.py tests/test_cards.py tests/test_scheduler.py -x -q`：**90 passed**，27.46s。
- 随后补充未知版本禁止切换的断言，`python -m pytest tests/test_reviews.py -q`：**21 passed**，7.94s。
- `app.openapi()` 构建通过；6个 review 路径，组件名唯一，未写入生成的 OpenAPI 或前端类型文件。

测试覆盖三个对象计划独立、无计划练习、练习状态不变、卡片/集合暂停与显式恢复、源回收站、测验有效性、原回执重试/参数冲突/revision、四档官方FSRS逐项对照与持久化往返、策略历史恢复/重启、时区/null/原因/分页/未来时间边界、owner隔离/CSRF、真实两个Session线程并发、复合FK/唯一对象CHECK/有效计划唯一及日志UPDATE拒绝。

冻结迁移回填测试在随机临时schema内执行真实c4和d58 DDL，验证草稿/归档卡片和归档集合不回填，结束回滚整个schema，不改已提交head。

本文件记录 executor 证据，不代替独立 reviewer 结论或用户浏览器验收。T21内容及复习前端由主执行者继续完成。

## 独立审核 P2 修复

审核复现 manual 未知算法版本/状态可以通过 switch→FSRS restart 被覆盖。新增三个真实PG参数用例：manual/999含未来状态、manual/1含未知非空状态、FSRS未知版本；所有拒绝都比较完整计划和完整日志列表保持不变。

- 修复前针对用例：**2 failed, 1 passed**，两个 manual 情况错误返回200。
- switch 开始处先对称验证当前策略的已知算法版本；manual 当前 scheduler_state 必须为 `{}`，FSRS历史独立存于 last_fsrs_state。验证通过前不修改计划。
- 修复后 `python -m pytest tests/test_reviews.py -q`：**24 passed**，9.26s。未修改DDL或备份schema。

## 质量审核 P2 调度状态修复

- manual适配器现在只接受严格空对象 `{}` 和显式 `manual/1`；之前允许在当前状态里携带 previous_fsrs 的纯测试已改为当前架构契约，历史由独立 last_fsrs_state 保留，已有真实PG切换测试继续验证历史不丢失。
- FSRS stability/difficulty 学习参数与 last_review 必须一起存在或一起为空；Review/Relearning仍必须有学习参数，保留未来时间、已知版本和状态结构校验。
- RED：新增纯回归 focused **9 failed, 3 passed**；真实PG manual损坏状态正式复习错误返回200并推进计划。
- GREEN：`python -m pytest --noconftest tests/test_scheduler.py -q`：**34 passed**；`python -m pytest tests/test_reviews.py -q`：**25 passed**，8.21s。损坏manual正式评价409，完整计划和日志均不变；三类FSRS状态的参数/last_review不一致均拒绝。
- 仅调度器及测试变化，无DDL、迁移或备份schema变化；隔离测试库已释放。

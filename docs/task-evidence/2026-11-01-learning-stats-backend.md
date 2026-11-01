# T24 学习活动、统计与日历后端本地证据

日期：2026-11-01。使用合成数据与真实 PostgreSQL，本地完成；未部署公网、未提交 Git、未调用模型或付费 API。

## 实现与数据边界

- 新增 `learning_events`，冻结迁移 `84c6e9b302df`，父版本 `f73b5d8a201c`。当前 public schema 共49表（含 `alembic_version`）。
- 列：`owner_id,event_id,kind,note_id,card_id,quiz_id,started_at,ended_at,request_hash,id,created_at`。同 owner/event_id 唯一；note/card/quiz 均通过 owner 复合外键约束。数据库 CHECK 强制恰好一个来源及匹配活动类型、区间正数且最多60秒。
- owner 行锁串行化批次、去重、配额；整批原子。同一 event_id 的标准化内容相同返回重复，不同返回409。已经接收的事件在离线窗口过后仍可幂等重放；新事件必须开始于过去24小时内且结束不晚于服务器现在。
- reading 仅关联 note；card_practice 仅 card；quiz 仅 quiz；review 可关联三者之一。新事件检查当前资源可访问性：非回收站笔记、confirmed 卡片且 active 卡组、confirmed 且非空测验。历史统计保留已发生事实，不因之后归档而抹去。
- 限制：批次1至100个事件；单 owner 每 UTC 日期最多10000条；每类原始事实查询最多50000条，超过返回明确422，建议缩短范围，不静默截断。配额超过返回429，整批回滚。

## 接口契约

均位于 `/api/v1/learning`；沿用登录账号、CSRF 与 Origin 校验。

```json
POST /events
{"events":[{"event_id":"UUID","kind":"reading","note_id":"UUID","started_at":"2026-11-01T12:00:00Z","ended_at":"2026-11-01T12:00:15Z"}]}
```

成功响应 `{"accepted":1,"duplicates":0}`；请求禁止多余字段，时间必须含时区，规范化为 UTC。其余资源以 `card_id` 或 `quiz_id` 替换 `note_id`。

`GET /stats?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` 返回：

```json
{"timezone":"Asia/Shanghai","date_from":"2026-11-01","date_to":"2026-11-01","reading_progress":{"recorded_notes":0,"active_notes_read":0,"active_notes_total":0},"days":[{"date":"2026-11-01","total_seconds":0,"reading_seconds":0,"reading_notes":0,"review_seconds":0,"practice_seconds":0,"quiz_seconds":0,"formal_reviews":0,"distinct_review_objects":0,"practice_count":0,"ratings":{"again":0,"hard":0,"good":0,"easy":0},"quiz":{"submitted":0,"graded":0,"needs_review":0,"actual_points":0,"max_points":0,"manually_corrected":0}}]}
```

- 当前 LearningSettings IANA 时区，缺省 `Asia/Shanghai`。默认截至当地今天的最近30天；显式范围含首尾最多93天、1900年至9998年。每天单独构造当地午夜 UTC 边界，支持23/25小时夏令时日。
- `total_seconds` 合并所有设备/页面有效时段；各 kind 独立合并，分类可重叠，不能相加得总数。`practice_seconds` 对应 `card_practice` 活动。
- `reading_notes` 是当地当天有正数 reading 区间重叠的 distinct 笔记数。`reading_progress.recorded_notes` 是本期阅读笔记去重总数，跨设备、跨日期只计一次；保留之后回收的历史事实，不能把每天的数值相加替代期间去重。`active_notes_read` 为其中当前仍未回收的本人笔记数；`active_notes_total` 为当前本人全部未回收笔记数。分子分母用同一 SQL 聚合快照计数，分子不超过分母；资源 ID 集合来自已有有界事件查询。此口径为“本期阅读覆盖”，不代表读完或掌握。
- 正式评价次数及每天 distinct item、临时练习次数、两类主动评价共同的四档分布来自 `review_logs`。create/pause/reschedule 等管理操作不计入。
- `quiz.submitted` 为交卷总数；graded/needs_review 为当前答案状态；actual_points 从当前 `quiz_answers.points` 重算，不依赖缓存的 attempt.total_points。manually_corrected 为当前 provenance=manual 的答案数，归属原交卷当地日期。max_points 为交卷快照满分。不输出掌握概率、不转换测验分数为 FSRS Rating。

`GET /calendar` 使用同样的日期参数与默认范围，可加 `kind=note|card|quiz`；返回 `timezone,date_from,date_to,kind,days`。每天字段为：

```json
{"date":"2026-11-01","completed":{"note":0,"card":0,"quiz":0},"practice":{"note":0,"card":0,"quiz":0},"quiz":{"submitted":0,"graded":0,"needs_review":0,"actual_points":0,"max_points":0,"manually_corrected":0},"due":{"note":0,"card":0,"quiz":0}}
```

completed/practice 为正式复习和临时练习历史，quiz 为测验交卷结果；due 仅计 `next_due_at >= now` 且落在查询日期内的 active 计划，并复用 `review_service.eligible_clause()` 排除回收站、草稿、归档卡组/对象。kind 筛选同时作用于历史及未来计划，note/card 筛选不混入测验交卷结果。

## 验证

执行位置 `D:\codex-pj\ai-notes\backend`，解释器 `backend/.venv/Scripts/python.exe`。唯一截断测试目标是 `127.0.0.1:55432/notes_test`；不访问公网或开发数据库。

- T24 新模块最终33例通过。覆盖原子批次、同批/跨批重复与409、不同 offset 同瞬间幂等、过期重放、新事件24h边界、未来/倒序/超长/naive时间、外加字段、跨 owner 与资源失效、并发去重、数据库复合 FK/CHECK。
- 以20个60秒短段构造两次10分钟、重叠5分钟活动，独立核验总900秒、reading和review各600秒；reading 不增加正式复习次数。
- 当地午夜平分15+15秒、修改时区重算30+0秒；America/New_York DST gap/fold 对应23/25小时边界且使用真实 UTC 经过时间。
- 同对象两次正式复习/一次临时练习计数与 distinct 对象、四档评价、简答等待人工复核、人工改成4/6分后重算（刻意将 attempt 缓存总分设0仍输出4）、历史回收站保留。
- 日历未来有效来源/卡组归档排除、按类型筛选、正式/临时历史分开、测验交卷结果；日期倒序、非法日期、93天含首尾边界、各事件/日志/计划/答案扫描 cap，及日配额超出整批回滚。
- 相关回归命令：`python -m pytest tests/test_learning_stats.py tests/test_cards.py tests/test_reviews.py tests/test_quizzes.py tests/test_scheduler.py tests/test_reminders.py tests/test_reminder_preferences.py -q`，235 passed，88.34s。
- 迁移从 f73 upgrade 到新 head，`alembic check` 无差异；另执行仅本测试库 `downgrade f73b5d8a201c -> upgrade head -> check`，使用冻结 DDL，无 metadata.create_all 依赖。
- 已有非阻断警告：FastAPI TestClient 所用 httpx 的 Starlette 弃用提示。

本地日志：`.local/m2/stats-final.log`、`stats-regressions.log`、`stats-migration.log`、`stats-schema.json`。首次测试暴露测试 fixture 尝试修改 append-only review_logs 和错误响应 envelope 假设；改为冻结日志创建时钟、按已有顶层 code 读取。未禁用或修改现有 trigger/hook。

独立 spec 复审发现 P2：只有 `date_to=0001-01-01` 时，原默认30天减法先于年份校验导致 OverflowError/500。已将显式日期年份检查移至减法前；两个端点分别验证 year 0001、1899、1900 下界邻近返回受控422，合法1900单日、1900-01-30默认窗口及普通最近30天保持200。修复后完整专项28 passed，11.32s；日志 `.local/m2/stats-year-boundary-fix.log`。未修改迁移或其他领域。

独立 quality 复审发现 P2：含时区的 datetime 极值可通过 AwareDatetime，但规范化 UTC 时发生下溢或上溢。已捕获 OverflowError 并转换为 Pydantic ValueError；真实 POST `/events` 验证 `0001-01-01T00:00:00+14:00` 与 `9999-12-31T23:59:00-14:00` 均返回422 validation_error，整批不写入，普通2026事件对照返回200。最终完整专项30 passed，12.25s；日志 `.local/m2/stats-utc-boundary-fix.log`。未改迁移、导出或备份 profile。

总完成审计补齐6.5阅读进度：新增上述每日阅读笔记数和期间阅读覆盖 DTO，无迁移。真实PG验证同一笔记多事件/多设备/跨日期去重、两篇阅读占三篇活跃笔记的2/3覆盖、回收后历史recorded仍2且活跃覆盖变1/2、还原后恢复2/3、其他owner只看到自己的1/1、review-only及无reading事件均为0且分母为真实未回收笔记数；跨窗口仅15秒正重叠的笔记计入，刚好在窗口边界结束/开始的其他笔记不计；原DST gap/fold用例新增每日distinct断言。最终完整专项33 passed，14.04s；日志 `.local/m2/stats-reading-progress.log`。未修改日历、事件表、迁移、导出或锁文件。

## 尚未覆盖

本证据仅为后端与真实PG验证；浏览器心跳可见性/闲置停止/退出队列隔离、前端展示及导出恢复集成由主任务分别验证。公网保持原部署。

# T21 测验后端执行证据

- 日期：2026-11-01；依据 `docs/superpowers/specs/2026-11-01-quizzes-design.md`。
- 范围：完整测验后端；不代表 M2 整体验收、浏览器验收或备份恢复已通过。
- 原工作区已有大量 tracked / untracked 改动，全部保留；无 commit / push / 部署。
- 隔离环境：`backend/.venv`；真实 PostgreSQL `localhost:55432/notes_test`；测试只用合成资料和 mock HTTP 模型。不读取或输出真实凭据，不调用真实 AI / OSS。

## 实现

- 五种严格题型：single_choice / multiple_choice / true_false / fill_blank / short_answer。严格选项、索引、布尔、空位、文本长度和分值校验；不接受模型来源字段。
- draft / confirmed / archived 手动测验；1–50 题原子替换，revision 冲突拒绝。空测验可保存草稿，确认必须至少一题。改题自动回到 draft；显式同请求 confirmed 时完整校验来源。
- 作答保存使用 attempt revision；每个成功保存同时增加 answer revision 和 attempt revision。开始请求携带页面所见 quiz revision，并保存首次完整脱敏收据；后续交卷/编辑不会改变相同开始请求的重放结果。`quiz_operations` 保存永久 UUID 请求收据及完整参数哈希，旧请求重放返回原结果且不覆盖新答案。
- attempt 包含题目、正确答案、分值和来源的冻结快照。作者可编辑参考答案；作答接口交卷前彻底省略 correct_answer / explanation 字段。改题生成新题目身份，不修改历史 attempt。
- 本地交卷：单选/判断精确匹配，多选集合完全匹配，填空逐项 trim/casefold 后整题全对才得分；空答零分，非空简答待复核。交卷没有模型调用，不转换 FSRS rating。
- 人工可复核任意已交卷答案，分数 0 至该题满分，必须说明理由；不可 UPDATE 的复核历史保存 old/new grade、owner 和请求身份，自动重算总分。
- 显式 AI 生成预览/确认：请求题型全部覆盖、数量完全匹配、整批 JSON 校验成功后一次发布草稿。共享现有 AiRun 的单次外发、租约、恢复、限流与用量记录。
- 显式简答 AI 评估：仅发送题干、参考答案、评分标准及当前用户答案；不自动发送原始文件或来源资料。输入 JSON 上限 30000 UTF-8 bytes；超限在预览阶段受控 422。score 为严格整数，needs_review 为严格布尔；不确定建议不计入确定得分，标注 ai_suggestion；人工可以覆盖。
- 调用前及应用结果时核对 owner / attempt revision / answer revision；晚到结果不能覆盖人工复核。已知模型用量保留。状态 GET 仅本地物化，不调用模型；未知收费结果不自动重发。
- 生成与评分专用会话不出现在普通会话列表，普通会话 GET、追加、相同 conversation request_key 再创建及 run GET 均拒绝。视觉 JSON 不追加普通聊天页码引用要求。
- 复习计划仍通过现有显式 POST 建立；归档测验在 review eligibility 查询中不可用；不修改笔记、卡片已有计划。

## 冻结迁移与数据保护交接

`e62a9c4f710b_quizzes.py` 为静态显式 DDL，父版本 d58a3b7c901e；不在迁移运行时读取 ORM metadata。保留 learning_quizzes 原身份表与 Review FK，新增七表，总计 45 个业务表 + alembic_version = 46。

| 表 | 关键 JSON / 身份 |
| --- | --- |
| quiz_questions | source：可空私有 frozen source；正确答案和 options 为 JSON |
| quiz_attempts | snapshot：题目数组，每项 id / position / kind / prompt / options / correct_answer / explanation / max_points / source；source 为私有 frozen source |
| quiz_answers | answer：用户答案 JSON；question_id 为历史题目身份，故意不要求题目仍在当前 quiz_questions 中 |
| quiz_operations | result：公开 AttemptOut 收据，questions[].source 是公开来源引用；交卷后含正确答案/解析；含 answers 与 reviews |
| quiz_answer_reviews | old_grade / new_grade：revision / status / points / feedback / provenance；owner_id 记录操作者事实 |
| quiz_generations | source：私有 frozen source；quiz_id 为成功物化结果 |
| quiz_gradings | input：question / reference_answer / rubric / max_points / student_answer，纯文本/分值，无来源 ID 或文件；result 为 score / feedback / needs_review |

视觉私有来源沿用 card_sources 的 pages 与 `_card_identity`，文字沿用历史文字版本/范围/哈希。公开来源不含 storage object key 或 private hash。

触发器：`quiz_answer_reviews_immutable BEFORE UPDATE` 调用现有 `reject_review_log_update()`；没有新增/放宽其他 guard。

恢复时：pending quiz_generations / quiz_gradings 应终态化为 failed，填写受控 error_code 和 finished_at，现有 queued/running AiRun 按既有中断规则处理；不要重新调用模型。in_progress quiz_attempts 必须保留其状态、revision、快照和已存答案，支持恢复后继续作答。历史 question_id 可能已不在当前题目表，需在 snapshot[].id / quiz_answers.question_id / 操作收据 questions[].id / answers[].question_id 之间一致保留或重映射。

## 固定 HTTP 合同

所有路径位于 `/api/v1/learning`：

- GET / POST `/quizzes`；GET / PATCH `/quizzes/{id}`。创建 `{title, questions?}`，PATCH `{revision, title?, status?, questions?}`。
- PUT `/quizzes/{id}/questions` `{revision, questions}`；题目源 GET `/quizzes/{id}/questions/{question_id}/source`。
- POST `/quizzes/{id}/attempts` `{revision, request_key}`；GET `/quiz-attempts` / `/quiz-attempts/{id}`。
- PUT `/quiz-attempts/{id}/answers/{question_id}` `{revision, request_key, answer}`；revision 始终是当前 attempt revision，返回完整 AttemptOut。
- POST `/quiz-attempts/{id}/submit` `{revision, request_key}`。
- POST `/quiz-attempts/{id}/answers/{question_id}/reviews` `{revision, request_key, points, reason, feedback?}`。
- GET `/quiz-attempts/{id}/questions/{question_id}/source` 读取历史快照来源。
- POST `/quiz-generations/preview` `{title, kinds, count, source, model?}`；POST `/quiz-generations` 在相同参数上添加 `{request_key, preview_hash, confirm_external:true}`。GET 列表/单项返回状态及成功 quiz_id。
- POST `/quiz-gradings/preview` `{attempt_id, question_id, revision, model?}`；POST `/quiz-gradings` 添加相同显式确认字段；GET `/quiz-gradings/{id}` 返回状态、模型用量和建议。

完整类型以 `quiz_schemas.py` 为准；OpenAPI 与前端类型由 root 统一生成。

## 验证

1. 首轮 `test_quizzes.py`：60 passed。
2. 扩展运行 T21 + cards + reviews + ai_chat + image_chat + pdf_chat：213 passed；一个既有 PDF migration fixture 因从仓库根目录执行而找不到相对 `alembic/versions` 路径，未涉及产品变更。
3. 从正确的 backend 工作目录运行扩充后的 T21 68 项 + 上述 PDF case：**69 passed，25.63s**。因此原跨模块 213 项与该既有路径失败已分别验证为 GREEN。
4. `compileall` 校验六个新增 quiz 模块与模型通过；静态迁移成功应用于真实 PG，保留既有 review log 函数/触发器。
5. 覆盖：五类型及错误类型/布尔冒充数字、50题原子修改/旧 revision/来源失败回滚、作答 DTO 脱敏、旧 quiz revision 快照、UUID 持久重放、真实双连接并发同 revision 仅一次成功、离线 GET 恢复、交卷幂等与零模型调用、空答/填空本地评分、人工分值边界/不可修改历史、AI 全题型/错误 JSON/伪造来源/NaN/错误 needs_review、晚到覆盖保护、账号停用/陈旧租约/终态用量、未知收费不重试、文字历史来源/视觉来源生成和普通聊天隔离。

复验命令（在 `backend`，仅限已授权的隔离测试库）：

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://notes:notes@localhost:55432/notes_test'
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -m pytest tests/test_quizzes.py -q
.venv/Scripts/python.exe -m pytest tests/test_cards.py tests/test_reviews.py tests/test_ai_chat.py tests/test_image_chat.py tests/test_pdf_chat.py -q
```

历史 RED：本次未保留“实现前测试失败”的独立运行，不能声称已有完整历史 TDD RED/GREEN 证据。当前实现和新增边界测试已 GREEN；独立规格/质量审查、前端浏览器验收、账号/整站恢复由 root 后续闭合。

测试库已在69项复验结束后明确释放给 root；后续不再操作该数据库，避免与 T23 / 数据保护验证冲突。


## 独立规格审查后修复（已复验）

- 开始合同补齐严格 quiz revision，拒绝其他页面重新确认后旧页面发起的新开始请求。
- 开始纳入 quiz_operations 永久收据，完整哈希包含 quiz_id / revision / request_key。重放先返回首次冻结 AttemptOut；后续保存/交卷不改变原响应，故不会在重复开始中新增正确答案字段。
- 新增回归覆盖开始旧版本保护、改变参数同键 409、交卷后重放脱敏、改题后既有收据稳定和新 revision 新请求成功。
- 无模型/迁移/备份 JSON 结构变化。另显式输出既有 scoring_policy 字段，避免 response_model_exclude_unset 将默认评分说明省略。
- root 授权短窗口后，在其已升级的 f73b5d8a201c / 48 表测试库上仅运行 pytest、未执行迁移或降级：最终 `tests/test_quizzes.py` **69 passed，25.16s**。包含开始收据与陈旧 quiz revision 复验。测试库再次明确释放给 root。


## 独立质量审查后修复：复核历史归属

- AnswerOut 新增必填 `id: UUID`，即 quiz_answers 行身份。客户端可用 reviews[].answer_id → answers[].id → question_id 稳定定位题目；不能依赖重复文本、得分或理由猜测关联。
- 新增 API 回归：两道相同简答题、相同用户答案、人工分数、理由和反馈，刷新后仍能从两个 review 的 answer_id 分别定位不同 question_id。
- 无 DDL 变化；`quiz_operations.result.answers[].id` 是新增嵌套身份，账号恢复须和 quiz_answers.id 一致映射。
- 无数据库 DTO 探针在修复前因缺少 id 出现 KeyError（RED），新增字段后相同探针 GREEN。真实 PG 全套复验待 root 分配测试库短窗口。

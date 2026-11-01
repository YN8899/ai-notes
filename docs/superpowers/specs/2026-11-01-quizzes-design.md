# T21 测验、作答与判分设计

承接持续完成M2的用户授权和T20引入的QuizIdentity。首版完整覆盖手动编辑/确认与AI草稿生成、五种题型、作答保存、确定判分和简答AI评估/人工复核；不是只加一个测验列表。

## 结构与工作流

learning_quizzes沿用owner/title/status/revision/question_count身份，题目表记录owner/quiz/position/kind/prompt/options/correct_answer/explanation/max_points/source；题型single_choice/multiple_choice/true_false/fill_blank/short_answer。单选正确索引、多选唯一索引数组、判断严格bool、填空按空位的非空字符串数组、简答非空参考答案。选择题2–8个非空选项、题数1–50、每题分数1–100，题目与原文/图片来源均有明确上限和所有者检查。填空使用 `{{blank}}` 占位，空位数与答案数一致。未知字段、布尔冒充数字、重复选项/索引、无效来源/分值一律拒绝。

测验可draft/confirmed/archived；题目批量编辑必须携带quiz revision，整批校验/事务替换，确认时校验全部题目与来源。题目修改不覆盖已开始作答的题目快照；旧attempt保留原测验版本、题目/参考答案/来源/最大分数快照。Quiz确认可加入自己的独立复习计划，任何分数不自动改变笔记/卡片/测验的FSRS评级。

quiz_attempts：owner/quiz/request_key/quiz_revision/status(in_progress/submitted/needs_review/graded)/revision/started/submitted/finished/snapshot。quiz_answers逐题保存answer/revision及判分状态/points/feedback/provenance，owner复合约束，唯一attempt+question身份。开放attempt接口仅给题干/选项，不提前返回correct_answer/explanation；作者编辑接口有答案是明确编辑行为。用户操作作答与编辑分开。

## 持久化与判分

开始、保存、交卷、复核都有request_key与revision；同请求重放对比完整参数，跨账号/归档/陈旧版本受控拒绝。逐题自动保存服务端，刷新/网络恢复后重新读取已保存答案；前端保留失败草稿，不能用旧回复覆盖新输入。交卷前等待全部保存；重复交卷不会重复判分或调用模型。

确定题本地按规则判分，不调用模型：选择/判断严格类型，多选集合完全相等，填空按空位逐项trim/casefold比较（不是数学等价判断），未答计0。支持人工复核所有题，填写分数与理由，保存不可覆盖的复核历史并重算总分。

简答可手工评分，也可显式预览后调用现有文本模型评估。新quiz_gradings记录attempt/所选答案版本、AiRun、request_key、状态、结果/用量。仅发送题干、参考答案、评分标准和该用户答案；无图片/原始文件自动外发。模型只返回受限JSON、score在题目上限内、feedback有界、needs_review为bool；不确定结果标记待复核，不伪造确定分数。可接收合法AI建议并明确标注来源，人工可确认/覆盖。所有新AI调用沿用持久单次AiRun、隐藏专用会话、禁止自动重发；调用期间答案/权限/attempt版本变化不覆盖新结果，已知用量保留。

## AI 生成与来源

预览选择现有文字选段/图片或单页PDF、题型/题数、模型与预计调用次数，用户明确外发确认才生成。沿用T19严格JSON/整体校验/草稿物化和request-key去重，不能从模型接受来源ID。生成和判分会话不能通过普通聊天读取/追加/重新创建。Worker恢复终止未知/中断调用，不自动收费重试。

来源按稳定身份/历史文字版本核验，失效时用户编写的测验/作答保留，禁止冒充当前原文或读取旧失效图。卡片、测验与源笔记之间仅追溯，不共享排期。

## 验证

合成内容与mock模型、真实PG：五类型非法/合法结构、答案接口脱敏、作答保存/断线重读/并发版本、重复交卷、确定判分/空答/人工复核重算、AI未知/损坏/超范围/needs_review与用量、来源/账号/答案变更中止、旧版本attempt完整、独立计划不串改。实际桌面/移动浏览器完成创建→AI草稿→审核→开始→逐题保存→刷新恢复→交卷→复核→来源。全部内容纳入账号与整站恢复，不能在数据保护未闭合时宣布M2完成。

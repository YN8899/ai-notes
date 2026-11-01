# T20/T22 独立复习计划与调度设计

沿用用户已批准的M2总架构与连续执行授权。T19卡片确认是排期入口，不能共用源笔记计划。正式排期和临时练习都保留事实日志，只有正式评价推进计划。

## 数据与边界

review_items：owner_id、kind(note/card/quiz)、note_id/card_id/quiz_id三个真实外键，CHECK确保恰一个非空且与kind一致，各引用使用owner复合FK，唯一owner+对象。不是多态字符串ID代替外键。为T21先建立完整Quiz基本身份表（owner、title、status/revision/timestamps），尚无测验内容不能排入复习；T21补合法题目与确认入口。

review_plans：owner/item、strategy manual/fsrs、status active/paused/archived、next_due_at、scheduler_state、algorithm_version、revision、updated_at；每item最多一个active或paused有效计划，归档旧计划可重新建。确认卡片建立自己的默认manual计划（到期为当前时间，用户可改），重复确认不新建；变回draft或归档/归档集合时暂停/隐藏相应有效计划，不复习草稿；恢复需显式重新启用。源笔记修改/归档不更新卡片计划，源笔记删除不删除用户卡片内容，只使来源不可读。

review_logs：owner、plan/item、request_key、action review/practice/reschedule/switch/pause/resume、rating、reviewed_at、scheduled_for、old/newdue、old/newstrategy、old/newstate、reason、plan_revision；同owner+request_key唯一，比较请求摘要，不允许同key不同参数。SQL owner→plan锁，revision防止两个标签同时推进。临时练习可以针对有效对象记录结果，不能修改plan的due/revision/FSRS状态。日志不可编辑覆盖。

笔记移入回收站只使其自身计划暂时不符合待办条件，不改原计划状态/排期/进度；恢复笔记后原 active 计划重新可见。用户已暂停的计划和因卡片/卡片集归档而暂停的计划均需显式恢复，不能因对象重新可见而自动改成 active。

## 接口与用户流程

统一复习中心提供到期/全部计划（类型/状态/日期筛选）、打开对象、四档主动回忆评价、临时练习、改下次时间、切换策略、暂停/恢复/归档及历史。笔记按钮“加入复习”；卡片确认自动加独立计划；测验确认后显式加入复习。T19前端保留卡片本身编辑，学习中心只读题面/翻面/作答，来源仍由原权限接口校验。

所有写入有CSRF、request_key、revision。查询不调用模型。时间输入含时区并存UTC，界面使用偏好IANA时区显示（T23完成前默认Asia/Shanghai），不把Date.toISOString本地字符串错误解释成UTC。

## 调度

已增加 `learning/scheduler.py`，使用官方 `fsrs==6.3.2`，实际算法版本标记 `py-fsrs/6.3.2`；四档Again/Hard/Good/Easy对应1/2/3/4，fuzz关闭以保证事务重试和测试可复现。状态JSON持久化，损坏或未知版本拒绝，不静默重置。manual评级须明确下次时间，并记录原因；FSRS评价按官方库计算。策略切换把旧值/状态写日志，并保存可恢复的历史FSRS状态；切回FSRS可明确选择恢复上次状态或新开始，选择和原因均记录，不丢旧日志。

主动评级适用于笔记/卡片/测验，测验分数不直接转换成记忆评级。不展示无依据的整篇笔记掌握概率。手动改到期不伪造算法学习，临时练习不改变算法状态。服务器使用实际当前UTC，客户端不可以伪造未来reviewed_at推进复习。

## 验证

真实PG并发两个Session/相同request_key/相同revision，恰一次推进；card/note/quiz各自计划，临时练习due/revision/state完全不变；草稿/归档/源回收站权限；复合FK、时间边界、manual/fsrs切换历史、四档序列与官方库对照。前端桌面/移动操作，退出及迟到响应隐藏；计划、日志、历史状态同步账号导出/恢复和整站备份。

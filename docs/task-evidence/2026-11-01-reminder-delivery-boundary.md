# T23 提醒投递的当地日期边界

2026-11-01，本地最小修复；不提交、不部署，不调用外部服务。

## 问题与边界

`dispatch()` 只生成当地今天的账本，但已提交的 `review.daily` Outbox 可以在宕机多日后才被 `deliver_outbox()` 消费，原实现会新建一条带旧日期计数的“今日待复习”通知，与“不补历史多天”的规格冲突。

投递时通过 `job_id + owner` 找到 `DailyDigest`，使用账本保存的时区核对本批 UTC 时刻对应的当地日期。过期、无匹配账本或损坏时区的事件，用现有 `delivered_at` 终结，不创建通知、不影响同批普通事件。账本、历史和已经生成的通知保留，不新增 schema。批处理时刻只取一次，日期判定与写入时间保持一致；事务、`skip_locked` 和 `Notification.event_id` 去重沿用原机制。

## 验证

- 新增真实 PostgreSQL 投递回归：同日延迟且仅一次；D 日提交未投递、D+2 重启；同批普通自检正常投递；已投递历史通知/旧账本保留；UTC 不变而当地跨日、UTC 跨日而当地仍当天；更改现偏好不影响账本时区；缺账本、owner 不匹配与损坏时区隔离。
- 原有固定 `NOW` 的投递测试显式冻结 `jobs.service.utcnow`，不依赖运行测试当天的日期。
- AST 语法与 `git diff --check` 已通过。
- 真实 PG RED：独立 Python 进程内替换为原 `deliver_outbox()`（不改共享文件），`pytest -q tests/test_reminders.py -k test_delivery_` 得到 **7 failed、1 passed、15 deselected / 3.77 秒**。D+2 重启时原实现把旧提醒和普通通知一起投递；UTC 日期不变而账本当地跨日时仍误发；无法匹配账本及损坏时区也盲发。
- 真实 PG GREEN：当前实现运行 `pytest -q tests/test_reminders.py tests/test_jobs.py`，**30 passed / 11.79 秒**，含全部 8 个新增投递用例及原 22 个提醒/任务用例。仅既有 Starlette/httpx 弃用 warning。两轮独占同一隔离 `notes_test`，结束后明确释放数据库窗口。
- 两轮均使用 `backend/.venv/Scripts/python.exe` 的进程内引导：先设 `Settings.model_config['env_file'] = None` 再执行 pytest；只用已授权合成测试连接，不加载真实 `.env`，未调用真实模型或外部推送。

本地详细日志：`.local/m2/reminder-delivery-final.log`（不上传 GitHub）。

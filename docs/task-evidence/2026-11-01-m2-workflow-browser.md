# M2 跨模块真实 App 浏览器合成闭环验收

日期：2026-11-01。结论：**PASS：桌面 24 条 + 390px 移动尺寸 24 条，共 48 条本次实际运行断言**。这是两个独立浏览器 context（上下文）各自从空数据开始执行的统一流程，不是此前卡片、测验、复习和统计验收数字的相加。

## 环境与边界

- 实际入口：`frontend/src/App.tsx`，Vite `http://127.0.0.1:5183/`；真实登录表单、App 导航、笔记 UI、上传控件、图片阅读器、OCR、搜索、卡片、测验、复习、偏好、主页通知及统计页面。
- 使用 `frontend-testing-debugging` skill。**Browser plugin not available**，采用已经缓存的 Playwright 与本机 Edge；没有新增浏览器依赖。
- 桌面 `1366×1000`；移动尺寸 `390×844`。后者是桌面浏览器 viewport（视口）模拟，**不是真手机验收**。
- API 与模型服务均由 Playwright route（请求拦截）提供连续内存状态。未连接实际后端、数据库、对象存储、真实模型或付费 API，也未使用真实账户或凭据。
- PNG 由独立空白浏览器页 Canvas 新生成，960×640，普通英文生物学习图片。文件通过真实文件输入和 App 的上传 API/XHR 路径传输；校验 PUT 收到的 bytes（字节）与选中的 PNG 完全一致。Canvas 只生成素材，没有绘制或替代 App。
- 未修改 React 内部状态、未导入 App 的 session helper 来绕过登录、未使用静态 harness（测试壳）冒充 App。仅用浏览器时钟推进 heartbeat（心跳）和 idle（无操作）时间；偏好持久化通过真实退出/重新登录复验。
- 自有 5183 Vite 在完成后已停止，端口已无监听；没有停止其他服务。

## 本次完整交互与结果

桌面与移动尺寸均逐项执行以下流程。

| 流程 | 实际观察与连续状态证据 | 结果 |
| --- | --- | --- |
| 登录与上传 | 合成账号从登录 UI 进入；新建图片笔记；选中新生成 PNG；申请上传、PUT、完成、processing→ready 轮询后真实图片可读。笔记 revision 1→2。 | PASS |
| OCR 与纠错 | 确认界面展示当前文件和笔记版本，确认前 0 次 OCR POST；明确确认后仅 1 次 POST；GET 轮询完成。保留 `Photosynthesls` 模拟原文，人工纠正为 `Photosynthesis` 并标记复核，纠错 revision 1→2；定位原页实际显示同一 PNG。 | PASS |
| 搜索与来源 | 搜索刚保存的 `Photosynthesis`，命中本次纠错稿；source 端点重新核验 note/OCR revision，显示匹配原文与同一图片；点击打开当前原笔记真实返回该笔记。 | PASS |
| 卡片 | 从同一图片笔记选择第 1 页；preview 不执行生成；明确外发确认后模拟生成 1 张 JSON 草稿，GET 轮询完成；人工阅读正面、翻面、确认；只建立该卡片独立复习计划，原笔记独立计划保持不变。 | PASS |
| 卡片活动边界 | 确认后的卡片实际读卡产生 `card_practice`，15 秒 heartbeat 发送片段；连续无操作超过 60 秒后停止累计，后续推进时钟不新增卡片活动。 | PASS |
| 测验 | 从同一图片页生成单选题+简答题；preview 不执行生成，明确确认后产生草稿；打开编辑器逐题核对答案、读取原来源，再保存整份并人工确认；两个答案逐题串行保存。 | PASS |
| 交卷与人工复核 | 正式交卷后客观题确定规则得 1 分，简答 `needs_review`、分数为 null；人工复核简答得 2 分，最终 3/3、`graded`，保留旧评分→新评分历史。没有创建或改变笔记/卡片复习排期。 | PASS |
| 四档复习与练习 | 同一卡片计划连续提交 rating 1/2/3/4，各 1 次，revision 1→5；下次时间分别为 11 月 2/3/4/5 日。临时练习只新增日志，计划、版本和算法状态完全不变；原笔记计划仍保持原样。 | PASS |
| 提醒和通知导航 | 将时区保存为 UTC 并关闭提醒，再开启；revision 1→2→3。退出并重新登录后，实际重新读取设置仍为 UTC/开启。生成历史与主页通知来自同一 mock 状态；通知 `target: reviews` 点击进入真实复习中心，显示 UTC。 | PASS |
| 统计和日历 | 从本轮真实 UI 产生的 15 条活动片段、4 条正式复习、1 条临时练习、1 份交卷及 1 条人工复核计算，展示当天正式完成卡片 4 次、练习 1 次、交卷 1 次、最终 3/3。四档各 1 次，去重复习对象 1 个；活动 union（区间并集）与各分类独立展示，明确不能相加。 | PASS |
| 阅读覆盖 | `reading_progress` 从本轮 `reading` 的 note_id 期间去重计算为 recorded 1、active read 1、active total 1；UI 显示 `1 / 1 篇` 和 `1 篇`，逐日 `reading_notes: 1`，并显示“有有效阅读活动不代表已读完或掌握；仅复习不计阅读覆盖”。未将每日数字相加当作期间唯一笔记数。 | PASS |

同一份状态持续驱动笔记列表/detail、页/file、OCR、搜索 source、图片来源目录、卡片/生成记录、测验/作答/复核、计划/历史、设置/提醒/通知、统计和日历。后续导航没有各自替换成相互矛盾的静态夹具。

## 浏览器质量检查

| 检查 | 桌面 | 移动尺寸 |
| --- | --- | --- |
| 页面身份、非空、实际 App | PASS | PASS |
| 框架错误 overlay（覆盖层） | 0 | 0 |
| runtime（运行时）console error / warning | 0 / 0 | 0 / 0 |
| 未处理 API 请求 | 0 | 0 |
| 远程网络请求 | 0 | 0 |
| 最终页面 document 横向溢出 | 无 | 无 |
| 实际运行断言 | 24/24 | 24/24 |
| 本轮被拦截 API 请求 | 192 | 190 |
| 本轮活动事件 | 15 | 15 |

已查看来源原图、复习历史、测验复核和最终统计截图；移动尺寸控件与文字正常换行，关键区域未见相互遮挡。此次只做测试与证据，没有产品代码变更，因此未另跑 build；产品静态和后端检查由对应实现/验收证据记录。

## 复跑与证据

项目根目录 PowerShell：

```powershell
# 单独终端，在 frontend 中启动本次专用端口
npm run dev -- --host 127.0.0.1 --port 5183 --strictPort

# 项目根目录执行；完成后停止上面的自有服务
node .local/qa-m2-workflow-20261101/workflow-qa.mjs
```

本地完整脚本、最终机器结果、日志和截图位于 `.local/qa-m2-workflow-20261101/`，不包含真实数据：

- `workflow-qa.mjs`：两种尺寸完整流程和连续 API 状态机。
- `results.json`：每条实际断言、全部非凭据请求、活动片段、最终资料/卡片/测验/计划/日志/设置/通知状态。
- `workflow.log`：本次最终 48 条 PASS 输出。
- `desktop-synthetic-source.png` / `mobile-synthetic-source.png`：选中并上传的 PNG。
- `desktop-ocr-source.png` / `mobile-ocr-source.png`：OCR 定位原页。
- `desktop-search-source.png` / `mobile-search-source.png`：搜索纠错稿并读回原图。
- `desktop-quiz-reviewed.png` / `mobile-quiz-reviewed.png`：完整作答及人工复核结果。
- `desktop-review-history.png` / `mobile-review-history.png`：独立计划、四档正式评价和不改排期的练习历史。
- `desktop-stats-final.png` / `mobile-stats-final.png`：真实 App 末端统计页面视口。
- `desktop-stats-facts.png` / `mobile-stats-facts.png`：活动、四档、最终测验结果和逐日事实区域。

早期运行留下的 `desktop-failure*` 为 QA 脚本选择器、数值 rating 映射和浏览器时钟/刷新行为调试产物，不是最终运行结果；最终权威结果为 `results.json` 两项 PASS 与 `workflow.log`。未据此声称产品先前 RED→GREEN 修复。

## 验收范围限制

本证据证明真实前端中的跨模块交互与 API 协议状态衔接。它不证明真实后端权限、数据库事务、worker、对象存储、模型质量/计费、服务器部署、跨设备并发或物理手机效果；相关真实后端合成闭环须引用独立后端验收证据。本次未调用任何付费模型，也未提交或推送 Git。

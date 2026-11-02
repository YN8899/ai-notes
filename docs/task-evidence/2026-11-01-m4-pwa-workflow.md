# T30 本地安装壳与推送开发证据

日期：2026-11-01。M4仍开发中；本记录区分代码/本地协议验证、真实浏览器、独立审查和外部待验。

## 当前实现与专项

已实现会话归属推送订阅、同账号显式重新关联设备、退出取消、通用锁屏内容、通知持久队列与租约隔离、VAPID配置、受控HTTPS外发；生产manifest/静态壳/安装与更新界面。新增 `POST /push/test` 与“发送测试提醒”入口：真实 `notification.test` 任务经Outbox落站内通知，仅为当前目标设备创建push任务，不同步外发、不伪造自检成功。

新增测试入口后：受影响后端扩大回归164项通过；追加producer回滚与token隔离后，push/auth/OpenAPI专项73项通过。前端全171项、类型与生产构建通过；OpenAPI与生成TypeScript连续两轮字节一致。范围有重叠，不相加为独立用例总数。原始记录为 `.local/m4/t30-test-entry-*.log`，后续外发门控修复须另行重跑，不能把这些结果当作未来源码的最终全量。

## 真实生产浏览器

Browser插件未提供，使用已缓存Playwright与Edge，真实生产App+FastAPI+PostgreSQL；回环5186→8015，合成QA库55436，默认禁用所有真实模型、OSS、推送。预览进程由工具管理的前台session启动，禁止读取真实.env。没有伪造API响应。

实际16项断言通过，见 `.local/m4/pwa-browser-results.json`：

- 登录后的真实个人设置与推送测试按钮，未配置时禁用授权/测试且保留站内提醒。
- 待复习计数从实际 `/api/v1/learning/review-due` 返回并显示0，非缺失数据占位。
- 当前生产壳114个静态资源，包含惰性编辑器JS与数学字体；CacheStorage没有API/认证响应。
- 断网刷新、新独立窗口可以重新加载壳；不会用缓存用户冒充服务器登录；联网恢复真实验证原会话。
- 390px无横向溢出，实际截图经人工查看；无框架错误覆盖、无未解释控制台异常。唯一控制台网络错误来自主动断网用例。

截图保存于本机Codex可视化目录，实际路径在机器结果中。它们是移动尺寸浏览器，不是物理手机。

另对真实Edge当前浏览器上下文设置通知权限为denied，再使用仅在受管理QA进程内生成、未写配置或日志的临时VAPID测试元数据。真实服务端显示enabled，点击“开启/重新关联本设备”后，界面明确回退站内提醒；没有订阅修改、测试排队或任何页面外部请求。结果 `pwa-permission-denied.json`。没有伪造API响应，也没有向真实推送服务发消息；它证明桌面浏览器拒绝分支，不证明物理手机弹窗。首次脚本未等配置加载、随后误将CDP权限施加到默认而非当前隐私上下文的两轮断言失败，均为测试配置问题；修正为等待真实配置响应并按当前browserContextId设权限后通过。

Edge DevTools在新的非隐私持久浏览器测试目录中 `Page.getInstallabilityErrors` 和manifest错误均为空，结果 `pwa-installability.json`。首次隐私上下文报告 `in-incognito`，原始结果保留为 `pwa-installability-incognito.json`；没有把隐私模式限制伪称为产品缺陷或忽略错误后直接通过。检查依据：[Chrome DevTools Page协议](https://chromedevtools.github.io/devtools-protocol/tot/Page/)。没有执行操作系统级安装。

## 审查发现与真实RED

1. 初轮控制台发现既有M3工作台请求漏 `/learning` 前缀，返回404；最小修复后相同真实浏览器流程通过，直接比对返回值与界面。原始命令输出保留在聊天工具记录；当时最新RED JSON后被UI文案变化用例覆盖，补记的明确来源观察为 `workbench-due-route-red.json`，不冒充未覆盖的原始文件。后续失败结果均另存时间戳副本。
2. 原计划的通用测试提醒入口缺失，独立规格审查拒绝通过，已补真实产品路径与API/事务/去重/当前设备限制测试。
3. 当前独立规格审查又复现：DNS解析期间租约失效后仍可开始POST。根任务也执行实际PushHttp/outbound源码的无数据库/无外网复现，得到 `checks=1, live=false, sent=true`，来源摘要与机器RED在 `push-dns-gate-red.json`。正在补解析后的最终租约/会话时间门控与有界DNS；尚不能将T30审查写PASS。

该DNS缺口随后已修，独立规格复审通过；根任务相同实际源码探针确认 `dns_resolved=true/live=false/sent=false/SendCancelled`，另有190项受影响后端通过。探针曾因新resolver签名的timeout参数未适配，在未到DNS时得到TypeError；该轮单独标为 `push-dns-gate-invalid-harness.json`，不作为GREEN。当前GREEN明确要求实际进入DNS、失效并由SendCancelled阻断。

独立质量审查进一步发现最后租约SQL期间跨本地午夜时，只重验会话截止、未重验每日提醒日期；实际源码AST+适配器隔离复现仍会发送旧日提醒。这项P2正在修复，质量审查仍未通过，不以规格前一轮通过替代最终结论。

## 数据结构

`e59a69fc0345`由两个独立全新迁移库与第三个实际dump/restore库测得67表/623列/20迁移。三库业务行均0，Alembic各1行；仅39处完整CHECK SQL实测拼写差异，其他结构精确一致，vector0.8.7及234对象匹配固定清单，1 routine/2 triggers完整一致。

新profile/guards已经正式登记；工具18项通过，根任务对实际QA库执行整套 `site_backup._schema`（只读）通过。原M3 profile和迁移字节未改。证据 `.local/m4/profile-measure/e59a69fc0345/`、`push-profile-tests.log`、`push-profile-registered-schema.json`。

## 待验边界

独立发送门控修复及复审、质量审查和最终冻结全量尚未完成；目标手机实际安装、权限拒绝的真机流程、真实VAPID外部推送送达也未验。开发替身中的实际加密/签名、Edge安装条件检查或移动视口均不能替代这些证据。其余T31–T34和最终M4恢复/性能/验收仍按总计划推进。

## 2026-11-02 收尾补记

以上“正在修复”“尚未完成”为2026-11-01分项记录当时时点，保留原始RED，不表示当前仍有相同阻断。最后租约SQL后的午夜/配置/密钥变化门控已由`t30-final-time-gate-green.log`的6项、`t30-final-time-gate-backend.log`的196项相关回归及后续冻结1487项后端全量覆盖。最新生产PWA16项、132静态资源通过，持久Edge安装条件/manifest均0错误；前端237项、类型/构建及契约冻结复验通过。完整范围和原始证据见[M4部署前本地收尾](2026-11-02-m4-pre-release.md)。独立总审计仍待复核；OS实际安装、目标手机及真实推送继续待验，按用户要求延后，不能将此补记视为整体验收或公网发布。

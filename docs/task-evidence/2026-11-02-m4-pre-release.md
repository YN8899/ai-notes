# M4 部署前本地收尾

日期：2026-11-02，Asia/Shanghai。本文记录部署前时点：T30–T34已进入正式源码，下列本地验证通过。当时独立审查与发布待执行；后续已通过独立本地发布审查并部署，见末尾补记及[公网发布证据](2026-11-02-m4-public-release.md)。M4真机与外部整体验收仍未完成。

用户本轮授权完成部署前检查并部署，本次未包含commit/push授权；真机验收延后，真实外部模型及推送未授权。本记录是部署前本地证据，未执行服务器、数据库或Docker操作。该时点根任务实查公网仍为原M2、HEAD `05857db`、49表；后续是否发布成功须以单独部署证据和私密运维记录为准，不能由本地74表或构建成功推断。

## 最终验证结果

| 范围 | 结果 | 本机原始证据（忽略目录 `.local/m4/`） |
|---|---|---|
| 冻结后端全量 | 1487 passed、0 skipped，348个源/测试/锁文件/profile摘要前后不变；6条既有Starlette/Alembic弃用警告保留 | `final-backend-frozen-result.json`、`final-backend-all-green.log`、`final-backend-source-before.json` / `after.json` |
| 最终前端Node | 35个测试文件，237 passed、0 failed、0 skipped；Node `v24.15.0`，`--test-concurrency=1` | `release-local-20261102/frontend-result.json`、`frontend-node.log` / `frontend-node-run.json` |
| 前端类型与生产构建 | `tsc --noEmit`、`vite build`退出0；沿用现有锁文件及已装依赖，保留大于500kB的构建块提示 | `release-local-20261102/frontend-typecheck.log`、`frontend-build.log`及对应`*-run.json` |
| 前端冻结与契约 | 202份前端源码/测试/public/构建配置摘要前后一致，且与原最终冻结快照一致；OpenAPI保持原冻结导出摘要，两次临时生成TypeScript均与正式文件字节一致 | `release-local-20261102/frontend-source-before.json` / `after.json`、`contract-result.json`、`api.generated.repeat-1.ts` / `repeat-2.ts`；原OpenAPI重复导出为`final-frozen-openapi.log`、`final-repeat-openapi.log` |
| 项目工具 | Windows 467项，461实际通过、6项POSIX条件跳过；另在独立Linux离线环境实际补证6项通过，不合并成同轮零跳过 | `final-root-tool-tests.log`、`root-tool-posix-six.log` |
| 两个生产镜像 | 原后端Dockerfile最终构建及前端镜像成功；74表严格profile、真实API/Caddy代理、Worker `--once`、132静态资源通过；空profile库业务行保持0 | `final-container-proof.json`、`final-container-web-smoke.json`、`final-container-proof.log` |
| 新旧数据恢复 | M4混合资料及旧v1–v4空/非空账号包9 passed；旧M3真实66表备份恢复再升级、空库完整迁移循环通过；旧65张业务表612列投影及13对象字节保持 | `final-data/evidence-index.json`、`final-data/mixed-site-evidence.json`、`final-data/m3-to-m4-and-empty-cycle.json`与日志 |
| 最后T31浏览器 | 真实生产前端/实际API/加密IndexedDB：图片、PDF、原件、裁剪21项；双标签草稿与同步禁用10项；无未处理运行错误 | `final_t31-files-browser-green.json`、`final_t31-draft-browser-green.json` |
| 离线异常与恢复阻断 | 队列异常10项；同步epoch变更UI原RED修复后正式3项、独立2项及实际浏览器6项GREEN，保留原操作编号、内容/导出和刷新后阻断 | `t31-queue-boundaries-browser-green.json`、`t31-epoch-browser-green.json`、`t31-epoch-quality-result.json` |
| 通知最后浏览器 | 275条同时间创建批次，真实双标签WS/HTTP补拉3项通过；已读保持head不变，revision由3到4，另一标签同seq窗口实际刷新 | `final_t34_readrev_browser_green.json` |
| PWA安装条件 | 生产PWA16项，132静态资源含PDFworker；持久Edge安装条件和manifest均0错误；未执行OS实际安装或物理手机验收 | `pwa-final-browser-results.json`、`pwa-final-installability.json` |
| 两真实API测量 | 1/10/50/100真实WS、每档约5秒；100连接10次SELECT尝试，约2.000次/秒，对照旧相同合成场景约296.819次/秒；共享Engine与独立raw auth分别统计 | `realtime-two-process-final.json` / `.log`、`realtime-two-process-baseline.json`；详见[原基线与追加测量](2026-11-01-m4-baseline.md) |

各组包含重叠用例，不能相加为独立测试总数。性能测试是单合成owner、多独立数据库会话的本地受控场景，不是生产容量、不同用户负载或延迟SLO。

## 本次前端复验与旧失败的区别

原 `final-frontend-frozen-node.log` 保留235 tests / 234 pass / 1 fail：`offline-workspace.test.mjs`对应的子进程尚未启动，Node运行器即在`ChildProcess.spawn`报`UNKNOWN`、errno `-4094`，没有执行该文件的产品断言。Windows并发进程资源问题是疑因，现有日志不足以确定底层原因，未据此修改产品代码。

本次先单独执行该文件，3项通过，再对全部35个文件设置`--test-concurrency=1`，237项零失败零跳过。串行设置只改变本地验证调度；没有安装依赖或改变测试、产品源码。旧失败日志未覆盖，新脚本与日志位于`release-local-20261102/`。

从项目根目录复现：

```powershell
node .local/m4/release-local-20261102/verify-frontend.mjs
```

该本机脚本先保存源摘要，再运行Node测试、两次生成临时TypeScript契约、类型检查和生产构建，最后逐字节摘要比较源与契约；构建只生成`frontend/dist`。等价构建链为项目`frontend/package.json`中的`tsc --noEmit && vite build`，实际命令、时间与退出码记录在`*-run.json`。运行时须保持产品源码冻结，避免与其他构建同时使用`dist`。

本次202文件摘要清单按`JSON.stringify(snapshot)`计算的SHA-256为`51800645bf0ab007fe2cd218c6555e72e6bd86baed88bfda9e8020c0b6245545`。这是摘要清单内容的摘要，不是任一源码文件摘要或包含文档的整库摘要。OpenAPI SHA-256为`e221c733b65f3375707f458175fd24013193fbd6b49438c8672ccd508ad21899`；正式与两次生成TypeScript均为`f602152d2d3463da46d438826826b92742127ee4fe74adefe02f8c7c7cb2496e`。

## 审查与剩余边界

T30 DNS后最终授权门、最后租约SQL后跨午夜/配置/密钥变化的原RED均保留；后续6项最终时间门GREEN、196项相关后端通过，独立规格/质量结论以各复审为准。T34两个P2（认证池隔离/排空关闭、同seq已读刷新）已修复并独立复验闭合，记录在`t34-quality/recheck-1/`；不能用旧“正在修复”段落冒充当前阻断，也不能把专项复核代替M4独立总审计。

- [x] 本地源码冻结、全量/构建/契约、镜像、恢复、最后浏览器和文档收尾。
- [x] 独立本地发布审查及最终部署差异复核：后续均PASS；M4全部用户验收仍单列待办。
- [x] 公网发布：后续已按授权完成，结果单记于[发布证据](2026-11-02-m4-public-release.md)。
- [ ] 真机/OS安装、目标手机通知权限及真实推送送达：按用户要求延后。
- [ ] 真实自定义模型、向量或其他外部付费调用：尚未授权，保留待验。

保留此前所有真实RED、工具配置失败和各次测试时点，不把后续GREEN倒写成早期已通过。部署使用完整当前Compose集合、原卷保护、只读监测快照及回退步骤，见项目私密运维记录；不将私密配置、凭据或账号清单写入公开文档。本轮发布不自动产生Git提交或推送。

## 后续发布补记

独立本地发布审查已通过；2026-11-02 13:45:19已部署并开放M3/M4，见[公网发布证据](2026-11-02-m4-public-release.md)。本记录前文保留部署前时点，真机和外部验收仍待完成。

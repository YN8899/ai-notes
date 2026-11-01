# T19 卡片与草稿审核设计

用户已批准四类卡片、AI草稿、批量审核与来源，并授权持续完成全部 M2。沿用现有学习域预留及私有来源，T19 不替代后续排期/测验任务。

## 用户流程

“学习卡片”管理私人卡片集；创建/编辑问答、填空、图片遮挡、公式卡片，正面/答案切换预览。卡片有 draft/confirmed/archived，手工可保存草稿或显式确认；AI只能保存草稿。批量编辑后按 revision 列表确认或归档，整批原子性处理，无跨账号/部分陈旧写入。卡片确认后 T20 为其建立独立计划，draft/archived 永不进入待复习。

类型约定：问答为非空 front/back；填空 front 含一个或多个非空 `{{答案}}` 标记，back补充说明；公式 front 为非空 LaTeX、back为答案/解释，渲染 trust=false 不执行HTML；遮挡卡为 front/back + payload.masks（1–20个归一化矩形），必须关联本账号有效普通图片或单页PDF，预览真正遮住指定区域，翻面解除，不仅显示坐标。每个文本字段限制长度，总卡片体积有上限，未知字段/类型/非法坐标拒绝。

来源可选。选择文字选段/图片页或矩形/单页PDF后服务器冻结来源，保留版本/页身份，不接受伪造外部URL、对象键或其他账号来源。文字沿用历史 NoteVersion 原文核验，当前版本变化显示历史标签；已删除/失效来源拒绝新读取/签名。图片源使用稳定 page_id 与 file/object身份，重排不串页。CardOut仅返回安全 public_source，服务器私有对象键不入浏览器。来源失效不销毁用户编辑卡片，标记需核对。

## 数据与接口

在 `app/modules/learning/` 增加 Deck、Card、CardGeneration，复用 Entity/owner复合唯一/FK、revision/updated_at及现有 errors/CSRF。新增 migration 固定DDL，parent b81d4f0a6c32，revision选 `c4d72e9a1f03`。Deck不硬删有卡片的集合，Card归档保留历史；分页/过滤有上限；批量最多50卡。

接口前缀 `/learning`：GET/POST decks、PATCH decks/{id}，GET/POST cards、PATCH cards/{id}，POST cards/batch-review，GET cards/{id}/source。生成：POST card-generations/preview（只读服务器校验，无模型）、POST card-generations（request_key、确认外发、deck_id、类型/数量/来源），GET card-generations 与/{id}。最终具体 schema 与样例由后端固定后提供前端。

## AI 生成

复用现有 AiConversation/AiRun、单次持久化调用和来源裁剪/选段，不另造自动重试框架。后台成功后将有界JSON回答严格解析为最多20张合法卡片，源来自服务器冻结记录，不能采用模型提供的源身份。所有卡片先进入 draft，完整校验后事务写入；无效或超量输出失败且保留用量，不静默截断。文本选段、普通图片/选区、单页PDF均可触发；遮挡仅支持图像源，矩形必须落在原始来源授权范围内。提示中的资料为不可信数据。

生成预览说明内容/页数/数量/模型，一次显式确认才调用；同 request_key 对比完整参数，重复点击不新增调用/卡片。生成会话不出现在普通聊天列表，不能被追加普通对话改变生成事实。终止/中断/未知结果不自动付费重放；旧Worker无租约/来源或用户失效时不得写成功草稿。CardGeneration状态与AiRun终态可恢复，幂等物化；GET轮询不会调用模型。

## 验证与数据保护

真实 PostgreSQL +模拟模型和合成图/文字，验证四类型结构、正反预览/遮挡、所有者、并发版本、批量原子性、来源失效/历史、AI JSON/数量防御、重复/租约/禁用/中断/用量。现有OCR/关键词/语义不回归。前端桌面及手机尺寸真实浏览器QA。

新增卡片/草稿是用户内容，必须纳入账号导出/恢复与整站备份，不能沿用语义缓存的排除策略。总 M2 最后完成新版格式与所有学习实体兼容，并在阶段证据明确未闭合项；不得在备份缺口存在时宣布M2完成。本轮无新真实模型费用或公网发布授权。

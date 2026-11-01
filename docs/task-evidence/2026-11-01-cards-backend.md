# T19 卡片后端验证记录

日期：2026-11-01。范围仅为 T19 后端；前端、T20 后续学习计划以及 M2 归档/备份由主任务继续完成。本记录不代表 M2 整体验收。

## 实现

- `backend/app/modules/learning/models.py`：`learning_decks`、`learning_cards`、`card_generations`，owner 复合 FK、revision、soft archive、generation+ordinal 唯一约束。
- 固定 DDL migration：`c4d72e9a1f03`，parent `b81d4f0a6c32`，已在真实隔离 PostgreSQL 16 + pgvector 测试库应用。
- `card_schemas.py`、`card_service.py`、`card_router.py`：问答、填空、遮挡、公式；draft/confirmed/archived；卡片集管理；有界分页；50 张批量确认/归档的整批版本核对。
- `card_sources.py`：复用文字历史 NoteVersion、稳定 image page_id、单页 PDF；校验原文件身份及 reading JPEG 实际 SHA-256，来源读取返回安全引用与历史标签，不返回 object_key。卡片列表在单次响应内对相同来源去重验证，关键写入/来源读取不使用该缓存。
- `card_generation.py`：预览摘要重核、显式外发确认、request_key 幂等、隐藏 AiConversation + AiRun 同事务、严格有界 JSON 解码、全部内容验证后原子写入 draft。Worker 与 GET 轮询可完成本地幂等物化，GET 不调用模型。失败、中断、失效租约、来源变化和停用用户不会自动重放付费调用。
- 既有 AI PDF/image 通道增加生成专用 JSON 指令及来源核验，保留 OCR/普通聊天行为；隐藏生成会话不进入聊天列表，也不能通过常规聊天详情、追加或重复创建接口访问。

## 接口约定

统一前缀 `/api/v1/learning`，精确 schema 以 `card_schemas.py` 为准。

- `GET/POST /decks`，`PATCH /decks/{id}`；`GET/POST /cards`，`GET/PATCH /cards/{id}`，`GET /cards/{id}/source`。
- 卡片创建：`{deck_id,kind,front,back,payload:null|{masks:[{x,y,width,height}]},status:'draft',source:null|Selection}`；PATCH 必须提供 `revision`。
- `Selection` 为 `{kind:'text',...TextInput}` / `{kind:'image',...ImageInput}` / `{kind:'pdf',...PdfInput}`，PDF 仅一页。遮挡坐标使用完整原页的归一化坐标，并限制在 source.region 内。
- `POST /cards/batch-review`：`{action:'confirm'|'archive',cards:[{id,revision}]}`，1–50 张，返回 CardOut 数组。
- 列表返回 `{items,total,offset,limit}`，limit 最大 50；卡片可按 deck_id/status/kind 过滤。
- `POST /card-generations/preview`：`{deck_id,kind,count,source,model:'deepseek-flash'}` 返回 `preview_hash`、安全 source、预计一次调用。
- `POST /card-generations` 增加 `{request_key,preview_hash,confirm_external:true}`；`GET /card-generations` 与 `GET /card-generations/{id}` 返回 pending/succeeded/failed、run_status、usage、error_code、card_ids 与安全 source。

## 验证证据

全程使用 `backend/.venv/Scripts/python.exe`、本机 `localhost:55432/notes_test` 可丢弃 PostgreSQL、local storage、合成图片与 fake 模型 key。模型请求通过 `httpx.MockTransport`，默认真实 provider client 被拒绝；没有真实模型/OSS 调用、没有提交/推送/部署。

RED：在实现前创建测试契约，直接导入 `app.modules.learning.card_schemas` 得到 `ModuleNotFoundError`。最初 pytest 尝试因隔离库尚未 migration 而在 fixture 阶段报 `UndefinedTable`，该次是环境失败，不能作为功能 RED。

GREEN：

```powershell
# 在 backend 执行；仅指向已获授权的可丢弃 notes_test
$env:DATABASE_URL='postgresql+psycopg://notes:notes@localhost:55432/notes_test'
./.venv/Scripts/python.exe -m alembic upgrade head
$env:TEST_DATABASE_URL=$env:DATABASE_URL
./.venv/Scripts/python.exe -m pytest tests/test_cards.py tests/test_ocr.py tests/test_ai_chat.py tests/test_image_chat.py tests/test_pdf_chat.py tests/test_text_selection.py -q --tb=short
# 168 passed，67.70 秒（扩展最终四个卡片用例之前）
./.venv/Scripts/python.exe -m pytest tests/test_cards.py tests/test_ai_chat.py -q --tb=short
# 70 passed，24.86 秒，含最终 47 个卡片用例
```

覆盖四类创建/编辑、坏字段/填空结构、mask 边界、owner/数据库复合 FK、revision、50 张批量陈旧原子失败、历史文字/删除/重排/同长度图片变更、AI 重复提交/20 张数量/JSON 重复键与非法值/模型伪造 source 字段、图片和 PDF 实际裁片、已知 usage 保留、未知结果不重放、过期租约、禁用用户的旧 ORM 对象刷新、重复物化及聊天隔离。仅出现现有 Starlette 测试客户端弃用 warning。

## 留给后续任务

T20 应在显式 confirmed 转换时创建独立学习计划，不能把 draft/archived 纳入待复习。当前未创建复习表或占位实现。主任务还须将全部学习实体与 visual source 私有身份纳入导出/恢复和整站备份后，才能报告 M2 完成。当前 ready FileVariant 不会由上传过期清理删除；来源笔记现有 API 为软删除，无需新增对象清理例外。

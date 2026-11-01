# T18 实施计划

对应 [设计](../specs/2026-10-31-semantic-search-design.md)。不提交、不推送、不部署、不调用真实模型。现有 T16/T17 未提交改动保留。

- [x] 盘点 T17、配置、Worker、备份与发布工作流，确认用户选用百炼。
- [x] 实现固定版本 pgvector 数据库镜像、本地/CI/发布支持，实际构建及 SQL 验证通过；工作流11项通过，独立规格/质量审查通过。
- [x] 实现后端配置/适配器、版本化索引与来源快照、持久化单次调用、构建/切换/回退/查询/来源接口及专项测试：最终专项38项通过，规格/质量复审通过。
- [x] 同步整站备份 schema、隔离恢复、账号缓存说明和测试；备份61项及CLI5项通过。
- [x] 实现可选语义搜索与索引管理前端，生成契约且再生成一致；96项测试/构建/27项合成界面检查通过。
- [x] 完成隔离迁移 head→c2→head/check、后端712项分组回归、前端96项/构建、运维449项（443通过/6环境跳过）、27项桌面/移动尺寸合成界面验收。
- [x] 独立规格审查、质量审查，修复身份缓存、租约锁顺序、5xx请求不确定性与旧版CLI；各项复审及最终跨层审查通过。
- [x] 更新 M2 状态、使用/发布/回退说明和证据；5178前端与55432隔离测试库已停用，临时子库均清理，保留本地QA记录。

接口约定：`/api/v1/semantic/status` GET；`/semantic/preview` GET（本地计算）；`/semantic/indexes` GET/POST（request_key UUID、manifest_hash、confirm_external=true）；`/semantic/indexes/{id}/activate` POST；`/semantic/queries` POST（request_key UUID、q、confirm_external=true、kind/notebook_id/folder_id/tag_id 可选）；`/semantic/queries/{id}` GET；`/semantic/chunks/{id}/source` GET。所有接口要求当前账号，写操作沿用 CSRF。状态与列表响应不包含密钥。最终接口以生成 OpenAPI 为准。

"""Versioned indexes and durable at-most-once paid-operation orchestration."""
from datetime import timedelta
from uuid import UUID
from sqlalchemy import select, update, cast, Float, case, func
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job, User
from app.shared.config import settings
from app.shared.errors import AppError
from app.modules.search.semantic_models import SemanticIndex, SemanticChunk, SemanticQuery, SemanticCall, Vector
from app.modules.search import semantic_schemas as s, semantic_sources as src, embedding_provider
from app.modules.search.keyword_schemas import SearchSourceOut


def configuration():
    cfg = settings()
    value = dict(provider="bailian", workspace=cfg.embedding_workspace, model=cfg.embedding_model,
                 dimensions=cfg.embedding_dimensions, chunker=src.CHUNKER)
    value["fingerprint"] = src.digest(value)
    return value


def require_provider(config=None):
    cfg = settings()
    if not cfg.embedding_enabled or not cfg.embedding_workspace or not cfg.embedding_api_key.get_secret_value():
        raise AppError(503, "semantic_unavailable", "语义搜索未启用或服务端尚未配置，请使用关键词搜索")
    if config and (config["provider"] != "bailian" or config["workspace"] != cfg.embedding_workspace
                   or config["model"] != "text-embedding-v4" or config["chunker"] != src.CHUNKER
                   or config["dimensions"] not in {64, 128, 256, 512, 768, 1024, 1536, 2048}):
        raise AppError(409, "semantic_config_incompatible", "活动索引与服务端配置不兼容，请重新建立索引")
    return configuration()


def _owner(db, owner):
    # CurrentUser can remain in this identity map across the committed paid-call
    # receipt. A lock alone does not refresh its cached status after an admin change.
    user = db.scalar(select(User).where(User.id == owner).with_for_update()
                     .execution_options(populate_existing=True))
    if not user or user.status != "active":
        raise AppError(401, "login_required", "会话已失效，请重新登录")


def synchronize(db, owner):
    rows = db.scalars(select(SemanticIndex).where(SemanticIndex.owner_id == owner,
                      SemanticIndex.status.in_(["queued", "building"]))).all()
    for row in rows:
        job = db.get(Job, row.job_id)
        if not job or job.status == "failed" or (job.status == "running" and job.lease_until <= utcnow()):
            row.status, row.error_code, row.finished_at = "failed", "semantic_interrupted", utcnow()
    db.flush()


def status(db, owner):
    synchronize(db, owner)
    rows = db.scalars(select(SemanticIndex).where(SemanticIndex.owner_id == owner)).all()
    cfg = settings()
    configured = bool(cfg.embedding_workspace and cfg.embedding_api_key.get_secret_value())
    result = s.SemanticStatusOut(enabled=cfg.embedding_enabled, configured=configured,
        available=cfg.embedding_enabled and configured, reason=None if cfg.embedding_enabled and configured else "semantic_unavailable",
        config=configuration() if cfg.embedding_workspace else None,
        active_index_id=next((r.id for r in rows if r.status == "active"), None),
        building_index_id=next((r.id for r in rows if r.status in {"queued", "building"}), None))
    db.commit()
    return result


def preview(db, owner):
    return s.SemanticPreviewOut(**src.manifest(db, owner, require_provider())[0])


def index_output(index):
    return s.SemanticIndexOut.model_validate(index, from_attributes=True)


def indexes(db, owner):
    synchronize(db, owner)
    rows = db.scalars(select(SemanticIndex).where(SemanticIndex.owner_id == owner)
                       .order_by(SemanticIndex.created_at.desc(), SemanticIndex.id.desc())).all()
    result = [index_output(row) for row in rows]
    db.commit()
    return result


def create_index(db, owner, data):
    _owner(db, owner)
    synchronize(db, owner)
    previous = db.scalar(select(SemanticIndex).where(SemanticIndex.owner_id == owner, SemanticIndex.request_key == data.request_key))
    if previous:
        if previous.manifest_hash != data.manifest_hash:
            raise AppError(409, "request_key_conflict", "同一个请求标识不能用于不同索引")
        result = index_output(previous)
        db.commit()
        return result
    config = require_provider()
    if db.scalar(select(SemanticIndex.id).where(SemanticIndex.owner_id == owner, SemanticIndex.status.in_(["queued", "building"]))):
        raise AppError(409, "semantic_build_in_progress", "已有索引正在构建，请等待完成")
    manifest, chunks = src.manifest(db, owner, config)
    if manifest["manifest_hash"] != data.manifest_hash:
        raise AppError(409, "semantic_manifest_changed", "资料或配置已变化，请重新预览并确认")
    job = Job(owner_id=owner, kind="semantic.build", dedupe_key="semantic:" + str(data.request_key),
              status="queued", payload={}, max_attempts=1)
    db.add(job)
    db.flush()
    row = SemanticIndex(owner_id=owner, request_key=data.request_key, status="queued", config=config,
                        manifest_hash=data.manifest_hash, source_count=manifest["source_count"],
                        chunk_count=len(chunks), job_id=job.id)
    db.add(row)
    db.flush()
    job.payload = {"index_id": str(row.id)}
    for ordinal, chunk in enumerate(chunks):
        db.add(SemanticChunk(owner_id=owner, index_id=row.id, ordinal=ordinal,
                             dimensions=config["dimensions"], **chunk))
    db.commit()
    return index_output(row)


def activate(db, owner, identity):
    _owner(db, owner)
    row = db.scalar(select(SemanticIndex).where(SemanticIndex.owner_id == owner, SemanticIndex.id == identity))
    if not row:
        raise AppError(404, "semantic_index_not_found", "索引不存在或无权访问")
    if row.status not in {"ready", "active"}:
        raise AppError(409, "semantic_index_not_ready", "索引尚未就绪")
    db.execute(update(SemanticIndex).where(SemanticIndex.owner_id == owner, SemanticIndex.status == "active",
               SemanticIndex.id != identity).values(status="ready"))
    row.status = "active"
    db.commit()
    return index_output(row)


def _usage(old, addition):
    result = dict(old or {})
    for key, value in addition.items():
        result[key] = result.get(key, 0) + value
    return result


def _lease(db, claimed):
    job = db.scalar(select(Job).where(Job.id == claimed["id"]).with_for_update())
    if (not job or job.status != "running" or job.worker_token != claimed["token"]
            or not job.lease_until or job.lease_until <= utcnow()):
        raise AppError(409, "semantic_lease_lost", "构建执行权已失效")
    return job


def _build_guard(db, index, claimed):
    # Match API/PDF lock order. Waiting for the owner must not hold Job and
    # prevent its heartbeat from renewing an otherwise valid lease.
    _owner(db, index.owner_id)
    job = _lease(db, claimed)
    if index.status not in {"queued", "building"}:
        raise AppError(409, "semantic_interrupted", "构建已中断")
    if require_provider() != index.config:
        raise AppError(409, "semantic_config_changed", "服务端配置变化，请重新建立索引")
    if src.manifest(db, index.owner_id, index.config)[0]["manifest_hash"] != index.manifest_hash:
        raise AppError(409, "semantic_sources_changed", "资料已变化，请重新建立索引")
    # Snapshot validation can outlast the lease while this transaction owns
    # Job. Recheck immediately before committing a paid-call receipt/publication.
    _lease(db, claimed)
    return job


def process_job(claimed):
    identity = UUID(claimed["payload"]["index_id"])
    try:
        with SessionLocal() as db:
            index = db.get(SemanticIndex, identity)
            if not index or index.job_id != claimed["id"]:
                raise AppError(409, "semantic_index_not_found", "索引不存在")
            chunks = db.scalars(select(SemanticChunk).where(SemanticChunk.index_id == identity)
                               .order_by(SemanticChunk.ordinal)).all()
            config = index.config
            chunk_ids = [c.id for c in chunks]
        for batch, offset in enumerate(range(0, len(chunk_ids), 10)):
            with SessionLocal.begin() as db:
                index = db.get(SemanticIndex, identity)
                _build_guard(db, index, claimed)
                if db.scalar(select(SemanticCall.id).where(SemanticCall.index_id == identity, SemanticCall.batch == batch)):
                    raise AppError(409, "semantic_call_already_started", "调用已开始，禁止自动重放")
                index.status = "building"
                db.add(SemanticCall(index_id=identity, batch=batch, status="started"))
                batch_chunks = db.scalars(select(SemanticChunk).where(SemanticChunk.id.in_(chunk_ids[offset:offset + 10]))
                                         .order_by(SemanticChunk.ordinal)).all()
                texts = [c.text for c in batch_chunks]
            # The durable receipt is committed before the only outbound POST.
            usage, error = {}, None
            try:
                vectors, usage = embedding_provider.embed(texts, config)
            except AppError as exc:
                usage, error = getattr(exc, "usage", {}), exc
            with SessionLocal.begin() as db:
                index = db.get(SemanticIndex, identity)
                receipt = db.scalar(select(SemanticCall).where(SemanticCall.index_id == identity, SemanticCall.batch == batch))
                receipt.usage, receipt.status = usage, "failed" if error else "succeeded"
                index.usage = _usage(index.usage, usage)
            if error:
                raise error
            with SessionLocal.begin() as db:
                index = db.get(SemanticIndex, identity)
                _build_guard(db, index, claimed)
                batch_chunks = db.scalars(select(SemanticChunk).where(SemanticChunk.id.in_(chunk_ids[offset:offset + 10]))
                                         .order_by(SemanticChunk.ordinal)).all()
                for chunk, vector in zip(batch_chunks, vectors, strict=True):
                    chunk.embedding = vector
                index.completed_chunks += len(vectors)
        with SessionLocal.begin() as db:
            index = db.get(SemanticIndex, identity)
            job = _build_guard(db, index, claimed)
            if index.completed_chunks != index.chunk_count:
                raise AppError(409, "semantic_incomplete", "索引未完成")
            index.status, index.finished_at = "ready", utcnow()
            job.status, job.result = "succeeded", {"index_id": str(identity)}
            job.worker_token, job.lease_until, job.error_code = None, None, None
        return True
    except AppError as error:
        with SessionLocal.begin() as db:
            index = db.get(SemanticIndex, identity)
            if index and index.status in {"queued", "building"}:
                index.status, index.error_code, index.finished_at = "failed", error.code, utcnow()
            job = db.get(Job, claimed["id"])
            if job and job.worker_token == claimed["token"] and job.status == "running":
                job.status, job.error_code = "failed", error.code
                job.worker_token, job.lease_until = None, None
        return False


def _query_owned(db, owner, identity):
    row = db.scalar(select(SemanticQuery).where(SemanticQuery.id == identity, SemanticQuery.owner_id == owner))
    if not row:
        raise AppError(404, "semantic_query_not_found", "查询不存在或无权访问")
    if row.status == "running" and row.created_at < utcnow() - timedelta(seconds=120):
        row.status, row.error_code, row.finished_at = "failed", "semantic_interrupted", utcnow()
        db.commit()
    return row


def _hit(chunk, source, score):
    snippet = chunk.text[:180] + ("…" if len(chunk.text) > 180 else "")
    fields = {k: source[k] for k in ("id", "kind", "note_id", "title", "note_kind", "matched_field",
              "source_revision", "ocr_revision", "page_id", "page_number", "region", "reviewed", "updated_at")}
    return s.SemanticHitOut(**fields, snippet=snippet, match_start=0, match_end=min(len(chunk.text), 180),
        match_hash=chunk.source_hash, chunk_id=chunk.id, score=max(-1.0, min(1.0, score)))


def query_output(db, row):
    items = []
    if row.status == "succeeded" and row.hits:
        chunks = db.scalars(select(SemanticChunk).where(SemanticChunk.owner_id == row.owner_id,
                          SemanticChunk.index_id == row.index_id,
                          SemanticChunk.id.in_([UUID(h["chunk_id"]) for h in row.hits]))).all()
        filters = {k: UUID(v) if k.endswith("_id") and v else v for k, v in row.payload.items()
                   if k in {"kind", "notebook_id", "folder_id", "tag_id"}}
        valid = src.current_chunks(db, row.owner_id, chunks, filters)
        by_id = {str(c.id): c for c in chunks}
        for hit in row.hits:
            chunk = by_id.get(hit["chunk_id"])
            if chunk and chunk.id in valid:
                items.append(_hit(chunk, valid[chunk.id], hit["score"]))
    return s.SemanticQueryOut(id=row.id, request_key=row.request_key, index_id=row.index_id, status=row.status,
        error_code=row.error_code, usage=row.usage, items=items, created_at=row.created_at, finished_at=row.finished_at)


def get_query(db, owner, identity):
    return query_output(db, _query_owned(db, owner, identity))


def create_query(db, owner, data):
    _owner(db, owner)
    payload = data.model_dump(mode="json", exclude={"request_key", "confirm_external"})
    request_hash = src.digest(payload)
    previous = db.scalar(select(SemanticQuery).where(SemanticQuery.owner_id == owner, SemanticQuery.request_key == data.request_key))
    if previous:
        if previous.request_hash != request_hash:
            raise AppError(409, "request_key_conflict", "同一个请求标识不能用于不同查询")
        return query_output(db, _query_owned(db, owner, previous.id))
    index = db.scalar(select(SemanticIndex).where(SemanticIndex.owner_id == owner, SemanticIndex.status == "active"))
    if not index:
        raise AppError(409, "semantic_no_active_index", "请先建立并激活语义索引")
    require_provider(index.config)
    filters = data.model_dump(include={"kind", "notebook_id", "folder_id", "tag_id"})
    src.validate_filters(db, owner, filters)
    row = SemanticQuery(owner_id=owner, request_key=data.request_key, request_hash=request_hash,
                        index_id=index.id, status="running", payload=payload)
    db.add(row)
    db.commit()  # Running is an at-most-once receipt, including process interruption.
    usage = {}
    try:
        vectors, usage = embedding_provider.embed([data.q], index.config)
        _owner(db, owner)
        require_provider(index.config)
        db.refresh(row)
        if row.status != "running":
            raise AppError(409, "semantic_interrupted", "查询已中断，请手动发起新请求")
        chunks = db.scalars(select(SemanticChunk).where(SemanticChunk.index_id == index.id,
                           SemanticChunk.owner_id == owner, SemanticChunk.dimensions == index.config["dimensions"],
                           SemanticChunk.embedding.is_not(None))).all()
        valid = src.current_chunks(db, owner, chunks, filters)
        # Filter current sources BEFORE pgvector's exact top-K. Stale chunks consume no slots.
        guarded_vector = case((func.vector_dims(SemanticChunk.embedding) == index.config["dimensions"],
                               SemanticChunk.embedding), else_=None)
        distance = cast(guarded_vector.op("<=>")(cast(vectors[0], Vector())), Float)
        ranked = db.execute(select(SemanticChunk.id, distance.label("distance")).where(
            SemanticChunk.index_id == index.id, SemanticChunk.owner_id == owner,
            SemanticChunk.dimensions == index.config["dimensions"], SemanticChunk.id.in_(valid))
            .order_by(distance, SemanticChunk.ordinal).limit(data.limit)).all()
        row.hits = [{"chunk_id": str(identity), "score": 1 - distance} for identity, distance in ranked]
        row.status, row.finished_at = "succeeded", utcnow()
    except AppError as error:
        usage = getattr(error, "usage", usage)
        row.status, row.error_code, row.finished_at = "failed", error.code, utcnow()
    except Exception:
        # Roll back a failed SQL transaction before preserving the known paid usage.
        identity = row.id
        db.rollback()
        with SessionLocal.begin() as receipt_db:
            receipt = receipt_db.get(SemanticQuery, identity)
            receipt.status, receipt.error_code, receipt.finished_at = "failed", "semantic_query_failed", utcnow()
            receipt.usage = usage
        return get_query(db, owner, identity)
    row.usage = usage
    db.commit()
    return query_output(db, row)


def source(db, owner, identity):
    chunk = db.scalar(select(SemanticChunk).where(SemanticChunk.id == identity, SemanticChunk.owner_id == owner))
    if not chunk:
        raise AppError(404, "search_source_not_found", "搜索来源不存在或无权访问")
    row = src.current_chunks(db, owner, [chunk]).get(chunk.id)
    if not row:
        raise AppError(409, "search_result_changed", "搜索内容或识别来源已变化，请重新建立索引")
    fields = {k: row[k] for k in ("id", "kind", "note_id", "title", "matched_field", "text", "source_revision",
              "ocr_revision", "page_id", "page_number", "file_id", "region", "reviewed")}
    return SearchSourceOut(**fields, match_start=chunk.start, match_end=chunk.end, match_hash=chunk.source_hash)

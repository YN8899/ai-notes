from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from app.modules.community import schemas as s, service
from app.modules.notes.schemas import RevisionIn, SearchText
from app.shared.dependencies import Admin, CurrentUser, DB

router = APIRouter(prefix="/community", tags=["community"])


@router.get("/posts", response_model=s.CommunityPostList)
def posts(db: DB, user: CurrentUser, mode: Literal["latest", "following", "recommended"] = "latest", subject: SearchText | None = Query(None, max_length=80), post_type: s.PostType | None = None, tag: SearchText | None = Query(None, max_length=32), cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=50)):
    return service.feed(db, user, mode=mode, subject=subject, post_type=post_type, tag=tag, cursor=cursor, limit=limit)


@router.get("/posts/mine", response_model=s.CommunityDraftList)
def mine(db: DB, user: CurrentUser, cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=50)):
    return service.list_drafts(db, user, cursor=cursor, limit=limit)


@router.post("/posts", response_model=s.CommunityDraftOut, status_code=201)
def create_post(data: s.CommunityPostCreate, db: DB, user: CurrentUser):
    result = service.create_post(db, user, data)
    db.commit()
    return result


@router.get("/posts/{post_id}", response_model=s.CommunityPostOut)
def post(post_id: UUID, db: DB, user: CurrentUser):
    return service.read_post(db, user, post_id)


@router.get("/posts/{post_id}/draft", response_model=s.CommunityDraftOut)
def draft(post_id: UUID, db: DB, user: CurrentUser):
    return service.draft_output(db, service.get_owned(db, user, post_id))


@router.patch("/posts/{post_id}", response_model=s.CommunityDraftOut)
def patch_post(post_id: UUID, data: s.CommunityPostPatch, db: DB, user: CurrentUser):
    result = service.update_post(db, user, post_id, data)
    db.commit()
    return result


@router.post("/posts/{post_id}/publish", response_model=s.CommunityPublishOut, status_code=202)
def publish(post_id: UUID, data: s.CommunityPublishIn, db: DB, user: CurrentUser):
    result = service.publish_post(db, user, post_id, data)
    db.commit()
    return result


@router.post("/posts/{post_id}/retry", response_model=s.CommunityPublishOut, status_code=202)
def retry(post_id: UUID, data: RevisionIn, db: DB, user: CurrentUser):
    result = service.retry_publish(db, user, post_id, data.revision)
    db.commit()
    return result


@router.post("/posts/{post_id}/withdraw", response_model=s.CommunityDraftOut)
def withdraw(post_id: UUID, data: RevisionIn, db: DB, user: CurrentUser):
    result = service.withdraw_post(db, user, post_id, data.revision)
    db.commit()
    return result


@router.get("/posts/{post_id}/assets/{asset_id}", response_class=Response,
    responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def asset(post_id: UUID, asset_id: UUID, db: DB, user: CurrentUser, kind: Literal["reading", "original"] = "reading"):
    data, mime, filename = service.read_asset(db, user, post_id, asset_id, kind)
    disposition = "inline" if kind == "reading" else "attachment; filename=community-file; filename*=UTF-8''" + quote(filename, safe="")
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Disposition": disposition})


@router.get("/posts/{post_id}/comments", response_model=s.CommunityCommentList)
def comments(post_id: UUID, db: DB, user: CurrentUser, cursor: str | None = Query(None, max_length=1024), limit: int = Query(30, ge=1, le=100)):
    return service.list_comments(db, user, post_id, cursor=cursor, limit=limit)


@router.post("/posts/{post_id}/comments", response_model=s.CommunityCommentOut, status_code=201)
def comment(post_id: UUID, data: s.CommunityCommentCreate, db: DB, user: CurrentUser):
    result = service.create_comment(db, user, post_id, data)
    db.commit()
    return result


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: UUID, db: DB, user: CurrentUser):
    service.delete_comment(db, user, comment_id)
    db.commit()
    return Response(status_code=204)


@router.put("/posts/{post_id}/like", status_code=204)
def like(post_id: UUID, db: DB, user: CurrentUser):
    service.set_reaction(db, user, post_id, "like", True)
    db.commit()
    return Response(status_code=204)


@router.delete("/posts/{post_id}/like", status_code=204)
def unlike(post_id: UUID, db: DB, user: CurrentUser):
    service.set_reaction(db, user, post_id, "like", False)
    db.commit()
    return Response(status_code=204)


@router.put("/posts/{post_id}/bookmark", status_code=204)
def bookmark(post_id: UUID, db: DB, user: CurrentUser):
    service.set_reaction(db, user, post_id, "bookmark", True)
    db.commit()
    return Response(status_code=204)


@router.delete("/posts/{post_id}/bookmark", status_code=204)
def unbookmark(post_id: UUID, db: DB, user: CurrentUser):
    service.set_reaction(db, user, post_id, "bookmark", False)
    db.commit()
    return Response(status_code=204)


@router.get("/bookmarks", response_model=s.CommunityPostList)
def bookmarks(db: DB, user: CurrentUser, cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=50)):
    return service.feed(db, user, bookmarked=True, cursor=cursor, limit=limit)


@router.post("/posts/{post_id}/reports", response_model=s.CommunityReportOut, status_code=201)
def report(post_id: UUID, data: s.CommunityReportCreate, db: DB, user: CurrentUser):
    result = service.create_report(db, user, post_id, data)
    db.commit()
    return result


@router.get("/moderation/reports", response_model=s.CommunityReportList)
def reports(db: DB, admin: Admin, status: Literal["pending", "dismissed", "hidden"] = "pending", cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=100)):
    return service.list_reports(db, admin, status=status, cursor=cursor, limit=limit)


@router.post("/moderation/reports/{report_id}/resolve", response_model=s.CommunityReportOut)
def resolve_report(report_id: UUID, data: s.CommunityReportResolve, request: Request, db: DB, admin: Admin):
    result = service.resolve_report(db, admin, report_id, data, request.state.request_id)
    db.commit()
    return result


@router.post("/moderation/posts/{post_id}", status_code=204)
def moderate(post_id: UUID, data: s.CommunityModerationIn, request: Request, db: DB, admin: Admin):
    service.moderate_post(db, admin, post_id, data.decision, data.reason, request.state.request_id)
    db.commit()
    return Response(status_code=204)

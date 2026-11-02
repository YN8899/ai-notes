from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.modules.notes.schemas import RevisionIn, SearchText
from app.modules.social import schemas as s, service
from app.shared.dependencies import CurrentUser, DB

router = APIRouter(prefix="/social", tags=["social"])


@router.get("/members", response_model=s.SocialMemberList)
def members(db: DB, user: CurrentUser, q: SearchText = Query("", max_length=32), cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=100)):
    return service.list_members(db, user, q=q, cursor=cursor, limit=limit)


@router.get("/members/{user_id}", response_model=s.SocialProfileOut)
def member(user_id: UUID, db: DB, user: CurrentUser):
    return service.profile_output(db, user, user_id)


@router.get("/profile", response_model=s.SocialProfileOut)
def profile(db: DB, user: CurrentUser):
    return service.profile_output(db, user, user.id)


@router.patch("/profile", response_model=s.SocialProfileOut)
def patch_profile(data: s.SocialProfilePatch, db: DB, user: CurrentUser):
    result = service.update_profile(db, user, data)
    db.commit()
    return result


@router.post("/friend-requests", response_model=s.FriendRequestOut, status_code=201)
def request_friend(data: s.FriendRequestCreate, db: DB, user: CurrentUser):
    result = service.request_friendship(db, user, data.user_id)
    db.commit()
    return result


@router.get("/friend-requests", response_model=s.FriendRequestList)
def friend_requests(db: DB, user: CurrentUser, direction: Literal["incoming", "outgoing"] = "incoming", cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=100)):
    return service.relationship_list(db, user, direction=direction, cursor=cursor, limit=limit)


@router.post("/friend-requests/{request_id}/respond", response_model=s.FriendRequestOut)
def respond(request_id: UUID, data: s.FriendRequestRespond, db: DB, user: CurrentUser):
    result = service.respond_friendship(db, user, request_id, data)
    db.commit()
    return result


@router.get("/friends", response_model=s.FriendRequestList)
def friends(db: DB, user: CurrentUser, cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=100)):
    return service.relationship_list(db, user, friends=True, cursor=cursor, limit=limit)


@router.delete("/friends/{user_id}", status_code=204)
def remove_friend(user_id: UUID, db: DB, user: CurrentUser):
    service.remove_friendship(db, user, user_id)
    db.commit()
    return Response(status_code=204)


@router.put("/follows/{user_id}", status_code=204)
def follow(user_id: UUID, db: DB, user: CurrentUser):
    service.set_follow(db, user, user_id, True)
    db.commit()
    return Response(status_code=204)


@router.delete("/follows/{user_id}", status_code=204)
def unfollow(user_id: UUID, db: DB, user: CurrentUser):
    service.set_follow(db, user, user_id, False)
    db.commit()
    return Response(status_code=204)


@router.get("/following", response_model=s.SocialMemberList)
def following(db: DB, user: CurrentUser, cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=100)):
    return service.list_members(db, user, following=True, cursor=cursor, limit=limit)


@router.post("/shares", response_model=s.NoteGrantOut, status_code=201)
def share(data: s.NoteGrantCreate, db: DB, user: CurrentUser):
    result = service.create_grant(db, user, data)
    db.commit()
    return result


@router.get("/shares", response_model=s.NoteGrantList)
def shares(db: DB, user: CurrentUser, direction: Literal["received", "sent"] = "received", cursor: str | None = Query(None, max_length=1024), limit: int = Query(20, ge=1, le=100)):
    return service.grant_list(db, user, direction=direction, cursor=cursor, limit=limit)


@router.get("/shares/{grant_id}", response_model=s.SharedNoteOut)
def read_share(grant_id: UUID, db: DB, user: CurrentUser):
    return service.shared_note(db, user, grant_id)


@router.delete("/shares/{grant_id}", status_code=204)
def revoke_share(grant_id: UUID, data: RevisionIn, db: DB, user: CurrentUser):
    service.revoke_grant(db, user, grant_id, data.revision)
    db.commit()
    return Response(status_code=204)


@router.get("/shares/{grant_id}/pages/{page_id}/image", response_class=Response,
    responses={200: {"content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}}})
def share_image(grant_id: UUID, page_id: UUID, db: DB, user: CurrentUser, kind: Literal["reading", "thumbnail"] = "reading"):
    content, mime = service.shared_image(db, user, grant_id, page_id, kind)
    return Response(content=content, media_type=mime, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Disposition": "inline"})


@router.get("/counts", response_model=s.SocialCountsOut)
def social_counts(db: DB, user: CurrentUser):
    return service.counts(db, user)

from app.shared.errors import AppError


def require_owner(user, resource):
    # Admin privileges do not bypass access to private resources.
    if resource is None or resource.owner_id != user.id:
        raise AppError(404, "not_found", "资源不存在或无权访问")
    return resource


def require_admin(user):
    if user.role != "admin":
        raise AppError(403, "admin_required", "需要管理员权限")
    return user


def audit(db, actor_id, action, request_id, resource_id=None, details=None):
    from app.shared.models import Audit
    db.add(Audit(actor_id=actor_id, action=action, request_id=request_id, resource_id=str(resource_id) if resource_id else None, details=details or {}))

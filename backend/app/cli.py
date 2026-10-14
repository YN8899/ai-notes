import argparse
import getpass
from sqlalchemy import select, update
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User, AuthSession
from app.shared.security import hasher
from app.shared.permissions import audit


def main():
    parser = argparse.ArgumentParser(description="管理员账户初始化和恢复")
    parser.add_argument("command", choices=["create-admin", "reset-admin"])
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    import re
    username = args.username.lower()
    if not re.fullmatch(r"[a-z0-9_-]{3,32}", username):
        parser.error("用户名要求 3 至 32 位字母、数字、下划线或连字符")
    password = getpass.getpass("密码（至少 10 位，不回显）：")
    if len(password) < 10 or len(password) > 128:
        parser.error("密码长度必须为 10 至 128")
    if password != getpass.getpass("再次输入密码："):
        parser.error("两次密码不一致")
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == username).with_for_update())
        if args.command == "create-admin":
            if user:
                parser.error("用户名已存在，不能提升现有普通用户")
            user = User(username=username, display_name="管理员", role="admin", password_hash=hasher.hash(password))
            db.add(user)
            db.flush()
        else:
            if not user or user.role != "admin":
                parser.error("管理员不存在")
            user.password_hash = hasher.hash(password)
            user.status = "active"
            db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
        audit(db, user.id, "cli." + args.command, "cli", user.id)
    print("管理员操作已完成")


if __name__ == "__main__":
    main()

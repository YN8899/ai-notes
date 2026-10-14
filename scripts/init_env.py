"""Create a local development .env without printing credentials."""
from pathlib import Path
import secrets

target = Path(__file__).resolve().parents[1] / ".env"
if target.exists():
    raise SystemExit(".env 已存在，未覆盖。")
password = secrets.token_hex(32)
content = (target.parent / ".env.example").read_text()
content = content.replace("replace-with-a-long-random-password", password)
target.write_text(content)
try:
    target.chmod(0o600)
except OSError:
    pass
print("本地开发配置已创建；密码未输出。")

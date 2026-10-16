"""Initialize an ignored env file's AI encryption secret without displaying it."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import tempfile

NAME = "AI_CREDENTIAL_ENCRYPTION_KEY"


def initialize(root: Path, relative_path: str) -> bool:
    root = root.resolve()
    target = root / relative_path
    if target.is_symlink() or target.name not in {".env", ".env.server"}:
        raise ValueError("Use a private .env or .env.server file")
    target = target.resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("Use an existing private env file inside the project")
    lines = target.read_text(encoding="utf-8").splitlines()
    matches = [index for index, line in enumerate(lines) if line.partition("=")[0].strip() == NAME]
    if len(matches) > 1:
        raise ValueError("Duplicate encryption-secret settings must be resolved first")
    if matches and lines[matches[0]].partition("=")[2].strip().strip("\"'"):
        return False
    assignment = NAME + "=" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if matches:
        lines[matches[0]] = assignment
    else:
        lines.append(assignment)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent,
                                         prefix=".env.ai-", delete=False) as output:
            temporary_path = Path(output.name)
            os.chmod(temporary_path, 0o600)
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description="初始化个人模型凭据的主加密密钥；不输出或覆盖已有密钥")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    try:
        changed = initialize(Path(__file__).resolve().parents[1], args.env_file)
    except (OSError, ValueError):
        parser.exit(1, "初始化失败，请检查项目内私密配置路径、重复设置和写权限。\n")
    print("主加密密钥已写入私密配置，未输出。请单独保管备份并重启 API。" if changed else "已有主加密密钥，未覆盖。")


if __name__ == "__main__":
    main()

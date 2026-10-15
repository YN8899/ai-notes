"""Create private server integration configuration without reading existing secrets."""
import argparse
import os
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RE = re.compile(r"crpi-[a-z0-9]+(?:-vpc)?\.cn-hangzhou\.personal\.cr\.aliyuncs\.com")
TAG_RE = re.compile(r"sha-[0-9a-f]{40}-[0-9]+-[0-9]+")


def create_env(template: Path, target: Path, registry: str, release_tag: str) -> None:
    if not REGISTRY_RE.fullmatch(registry):
        raise ValueError("Registry must be the Hangzhou ACR personal instance hostname")
    if not TAG_RE.fullmatch(release_tag):
        raise ValueError("Use the release tag printed by the ACR publishing workflow")
    password = secrets.token_hex(32)
    replacements = {
        "ACR_REGISTRY": registry,
        "RELEASE_TAG": release_tag,
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg://notes:{password}@db:5432/notes",
    }
    lines = template.read_text(encoding="utf-8").splitlines()
    content = "\n".join(
        f"{key}={replacements[key]}" if (key := line.partition("=")[0]) in replacements else line
        for line in lines
    ) + "\n"
    # O_EXCL prevents a second run or race from replacing the existing database password.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Ubuntu 私有开发联调配置，不覆盖已有文件")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--release-tag", required=True)
    args = parser.parse_args()
    try:
        create_env(ROOT / "deploy/server.env.example", ROOT / ".env.server", args.registry, args.release_tag)
    except FileExistsError:
        parser.exit(1, ".env.server 已存在，未读取或覆盖。更新时保留现有密码。\n")
    except (ValueError, OSError):
        parser.exit(1, "生成失败：请检查 registry、发布标签、模板文件和目录写权限。\n")
    print(".env.server 已创建；数据库密码未输出。请自行填写 OSS 凭据，再执行部署检查。")


if __name__ == "__main__":
    main()

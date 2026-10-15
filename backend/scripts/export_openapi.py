import json
from pathlib import Path
from app.main import app

Path("openapi.json").write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
    newline="\n",
)

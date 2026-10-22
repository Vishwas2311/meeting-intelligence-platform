"""Export the actual service schema without opening a DB or contacting cloud services."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    from app.main import app

    path = ROOT / "docs" / "guides" / "openapi.json"
    path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(app.openapi()['paths'])} API paths to docs/guides/openapi.json")

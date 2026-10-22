"""Fail the backend gate if a successful operation has no usable response schema."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate(schema):
    count = 0
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "patch", "put", "delete"}:
                continue
            count += 1
            for code, response in operation["responses"].items():
                if not code.startswith("2") or code == "204":
                    continue
                content = response.get("content", {})
                assert content, f"Missing media contract: {method} {path} {code}"
                assert all(entry.get("schema") for entry in content.values()), (
                    f"Empty response schema: {method} {path} {code}"
                )
    return count


if __name__ == "__main__":
    from app.main import app

    schema = app.openapi()
    count = validate(schema)
    published = json.loads((ROOT / "docs/guides/openapi.json").read_text(encoding="utf-8"))
    assert schema == published, "Export OpenAPI after a contract change"
    print(f"PASS: {count} operations have response media/schema contracts; export matches runtime")

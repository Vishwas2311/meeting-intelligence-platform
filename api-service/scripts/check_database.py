"""Redacted selector/inventory; opt-in read-only Oracle connectivity, never DDL."""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def report(connect=False):
    from app.config import get_settings

    settings = get_settings()
    backend = make_url(settings.database_url).get_backend_name()
    result = {
        "profile": settings.app_env,
        "selected_backend": backend,
        "database_url_environment_override": "DATABASE_URL" in os.environ,
        "private_environment_file_present": (ROOT / ".env").is_file(),
        "sqlite_required_by_oracle_runtime": False,
        "oracle_connectivity": "NOT_RUN",
        "private_values_emitted": False,
    }
    if connect:
        if backend != "oracle":
            result["oracle_connectivity"] = "NO_ORACLE_TARGET_CONFIGURED"
        else:
            from sqlalchemy import text

            from app.database import engine

            try:
                with engine.connect() as connection:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    assert connection.scalar(text("SELECT 1 FROM DUAL")) == 1
                    result["oracle_connectivity"] = "PASS"
                    result["oracle_version"] = list(engine.dialect.server_version_info or ())
                    result["schema_head_matches"] = (
                        connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0002_production"
                    )
            except Exception as error:
                result["oracle_connectivity"] = "FAIL"
                result["error_type"] = type(error).__name__
            finally:
                engine.dispose()
    return result


def sqlite_inventory():
    inventory = []
    for current, directories, names in os.walk(ROOT):
        directories[:] = [
            d
            for d in directories
            if d
            not in {
                ".venv",
                "node_modules",
                "var",
                "output",
                "dist",
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                "test-results",
                "playwright-report",
            }
        ]
        for name in names:
            path = Path(current) / name
            if name.startswith(".env") and name != ".env.example":
                continue
            if path.suffix not in {
                ".py",
                ".yaml",
                ".yml",
                ".md",
                ".txt",
                ".ini",
                ".json",
                ".example",
            }:
                continue
            lines = [
                i
                for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1)
                if "sqlite" in line.lower()
            ]
            if lines:
                inventory.append({"file": path.relative_to(ROOT).as_posix(), "lines": lines})
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = report(args.connect)
    if args.inventory:
        result["sqlite_references"] = sqlite_inventory()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "sqlite_references"}))
    raise SystemExit(
        1
        if result["oracle_connectivity"] in {"FAIL", "NO_ORACLE_TARGET_CONFIGURED"}
        or result.get("schema_head_matches") is False
        else 0
    )

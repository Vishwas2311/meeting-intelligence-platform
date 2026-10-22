"""Build the source-only backend handover from tracked delivery files."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    repository = ROOT.parent
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repository, check=True, capture_output=True
    )
    names = [
        name
        for name in result.stdout.decode().split("\0")
        if name
        and not name.startswith("api-service/frontend/")
        and name not in {"api-service/tests/ui_bridge.py", "api-service/tests/ui_server.py"}
    ]
    assert names, "Stage the reviewed source files before creating a handover"
    output = ROOT / "dist/meeting-intelligence-backend.zip"
    output.parent.mkdir(exist_ok=True)
    hashes = {}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            path = Path(name)
            assert not path.is_absolute() and ".." not in path.parts
            assert not any(
                part
                in {
                    ".git",
                    ".venv",
                    "frontend",
                    "var",
                    "output",
                    "dist",
                    "secrets",
                    "wallet",
                    "node_modules",
                }
                for part in path.parts
            )
            assert path.name != ".env" and not (
                path.name.startswith(".env.") and path.name != ".env.example"
            )
            data = (repository / name).read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (0o100755 if name == ".githooks/pre-commit" else 0o100644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
        archive.writestr("HANDOVER_SHA256.json", json.dumps(hashes, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert all(
            hashlib.sha256(archive.read(name)).hexdigest() == value
            for name, value in hashes.items()
        )
    print(f"Created {output.name}: {len(hashes)} tracked source files; no local data or secrets")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Compatibility flag; this delivery is always backend-only",
    )
    parser.parse_args()
    build()

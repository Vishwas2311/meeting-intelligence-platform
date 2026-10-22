"""Validate a restricted committed-audio manifest against a restored audio root."""

import argparse
import hashlib
import json
from pathlib import Path


def verify(manifest, root):
    root = Path(root).resolve()
    seen = set()
    for item in manifest["audio"]:
        key = item["key"]
        target = (root / key).resolve()
        if (
            key in seen
            or not target.is_relative_to(root)
            or Path(key).is_absolute()
            or not target.is_file()
        ):
            raise RuntimeError("Unsafe, duplicate or missing recovery audio")
        seen.add(key)
        digest = hashlib.sha256()
        with target.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        if target.stat().st_size != item["bytes"] or digest.hexdigest() != item["sha256"]:
            raise RuntimeError("Recovery audio checksum/size mismatch")
    return len(seen)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("audio_root", type=Path)
    args = parser.parse_args()
    try:
        count = verify(json.loads(args.manifest.read_text()), args.audio_root)
    except Exception:
        raise SystemExit("Recovery manifest validation FAILED; no changes made") from None
    print(
        f"Recovery audio verified: {count} files; database/key/RPO/RTO qualification still required"
    )

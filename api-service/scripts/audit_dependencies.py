"""Query OSV for resolved public package versions; no source or credentials are uploaded."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def audit(include_frontend=True):
    packages = set()
    for line in (
        (ROOT / "requirements.lock").read_text(encoding="utf-8-sig")
        + "\n"
        + (ROOT / "requirements.linux.lock").read_text(encoding="utf-8")
    ).splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.split("==", 1)
            packages.add(("PyPI", name, version))
    if include_frontend:
        for path, item in json.loads((ROOT / "frontend/package-lock.json").read_text())[
            "packages"
        ].items():
            if "node_modules/" in path and item.get("version"):
                packages.add(
                    ("npm", item.get("name") or path.rsplit("node_modules/", 1)[1], item["version"])
                )
    ordered = sorted(packages)
    queries = [
        {"package": {"ecosystem": ecosystem, "name": name}, "version": version}
        for ecosystem, name, version in ordered
    ]
    request = Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        results = json.load(response)["results"]
    findings = [
        {
            "ecosystem": ordered[index][0],
            "package": ordered[index][1],
            "version": ordered[index][2],
            "id": vulnerability["id"],
        }
        for index, result in enumerate(results)
        for vulnerability in result.get("vulns", [])
    ]
    return {
        "source": "https://api.osv.dev/v1/querybatch",
        "checked_at": datetime.now(UTC).isoformat(),
        "queries": len(queries),
        "vulnerabilities": findings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output/implementation/dependency-audit.json"
    )
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()
    result = audit(include_frontend=not args.backend_only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"Audited {result['queries']} resolved public packages; findings={len(result['vulnerabilities'])}"
    )
    raise SystemExit(1 if result["vulnerabilities"] else 0)

"""Validate the one-VM Compose template using dummy paths/digests without a daemon or secrets."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix="meeting-compose-check-") as temporary:
        root = Path(temporary)
        for name in ["audio", "secrets", "tls"]:
            (root / name).mkdir()
        environment = root / "production.env"
        environment.write_text((ROOT / "deploy/api.env.example").read_text(), encoding="utf-8")
        values = {
            **os.environ,
            "BACKEND_IMAGE": "meeting/backend@sha256:" + "0" * 64,
            "WEB_IMAGE": "meeting/web@sha256:" + "1" * 64,
            "GATEWAY_IMAGE": "meeting/gateway@sha256:" + "2" * 64,
            "AUDIO_VOLUME": str(root / "audio"),
            "SECRET_DIRECTORY": str(root / "secrets"),
            "TLS_DIRECTORY": str(root / "tls"),
            "PRODUCTION_ENV_FILE": str(environment),
        }
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "deploy/compose.api.yaml"),
                "config",
                "--quiet",
            ],
            cwd=ROOT,
            env=values,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("Production Compose template validation failed")
    report = {
        "compose_template": "PASS",
        "topology": "one application VM, API-only gateway/API/one worker, external Oracle",
        "private_values_used": False,
        "runtime_image_qualification": "separate required check",
    }
    destination = ROOT / "output/implementation/deployment-template.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    run()

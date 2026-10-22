"""Ponytail quality gate used by the local hook and CI; no deployment or live data."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, directory=ROOT):
    result = subprocess.run(args, cwd=directory, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Critical regressions plus frontend checks; full suite remains a release gate",
    )
    parser.add_argument(
        "--backend-only", action="store_true", help="No Node or testing UI required"
    )
    parser.add_argument("--node", default=os.getenv("NODE_EXE") or shutil.which("node"))
    args = parser.parse_args()
    if not args.backend_only:
        if not args.node:
            configured = subprocess.run(
                ["git", "config", "--local", "--get", "meeting.nodeExecutable"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            args.node = configured.stdout.strip() if configured.returncode == 0 else None
        if not args.node:
            raise SystemExit(
                "Node.js is required. Put Node on PATH or set NODE_EXE to its executable."
            )
    run([sys.executable, "-m", "ruff", "check", "app", "tests", "scripts", "migrations", "load"])
    run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "app",
            "tests",
            "scripts",
            "migrations",
            "load",
        ]
    )
    run([sys.executable, "-m", "pip", "check"])
    run([sys.executable, "scripts/check_api_contract.py"])
    tests = (
        [
            "tests/test_production_safeguards.py",
            "tests/test_migrations.py",
            "tests/test_history_migration.py",
            "tests/test_release_controls.py",
            "tests/test_failure_recovery.py",
            "tests/test_retention_batches.py",
            "tests/test_backend_readiness.py",
            "tests/test_blocker_resolution.py",
        ]
        if args.quick
        else []
    )
    (ROOT / "output/implementation").mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *tests,
            "--junitxml=output/implementation/hook-tests.xml"
            if args.quick
            else "--junitxml=output/implementation/backend-final.xml",
        ]
    )
    if not args.backend_only:
        frontend = ROOT / "frontend"
        run([args.node, "node_modules/prettier/bin/prettier.cjs", "--check", "."], frontend)
        run([args.node, "node_modules/typescript/bin/tsc", "--noEmit"], frontend)
        run([args.node, "--test"], frontend)
        if not args.quick:
            run([args.node, "node_modules/vite/bin/vite.js", "build"], frontend)
    print(
        "Quality gate passed. External Oracle/IdP/Linux-image/load/restore qualification remains separate."
    )


if __name__ == "__main__":
    main()

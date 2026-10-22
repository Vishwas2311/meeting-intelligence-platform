"""Ponytail quality gate used by the local hook and CI; no deployment or live data."""

import argparse
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
        help="Critical backend regressions; full suite remains a release gate",
    )
    parser.add_argument(
        "--backend-only", action="store_true", help="Compatibility flag; this package is API-only"
    )
    args = parser.parse_args()
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
    print(
        "Quality gate passed. External Oracle/IdP/Linux-image/load/restore qualification remains separate."
    )


if __name__ == "__main__":
    main()

"""Inspect expired pending uploads; --apply quarantines bytes and releases reservations."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.reconciliation import reconcile_uploads

    print(json.dumps(reconcile_uploads(args.apply)))

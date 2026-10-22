"""Reconcile a synthetic load ledger against the running authenticated test API."""

import argparse
import json
import os
from pathlib import Path

import httpx


def check(client, ledger):
    for expected in ledger:
        headers = {"Authorization": "Bearer " + expected["token"]}
        response = client.get(
            f"/api/meetings/{expected['meeting_id']}/transcript?limit=500", headers=headers
        )
        response.raise_for_status()
        value = response.json()
        segments = value["segments"]
        while value.get("next_cursor"):
            value = client.get(
                f"/api/meetings/{expected['meeting_id']}/transcript",
                params={"after": value["next_cursor"], "limit": 500},
                headers=headers,
            ).json()
            segments.extend(value["segments"])
        if not set(expected.get("event_ids", [])) <= {item["client_event_id"] for item in segments}:
            raise RuntimeError("An acknowledged transcript event is missing")
        for jid in expected.get("job_ids", []):
            job = client.get(f"/api/meetings/{expected['meeting_id']}/jobs/{jid}", headers=headers)
            job.raise_for_status()
            if job.json()["status"] != "completed":
                raise RuntimeError("An accepted job did not complete")
    return len(ledger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ledger",
        type=Path,
        help="Restricted synthetic ledger including owner tokens; never publish it",
    )
    args = parser.parse_args()
    with httpx.Client(base_url=os.environ["LOAD_TEST_BASE_URL"], timeout=30) as client:
        count = check(client, json.loads(args.ledger.read_text()))
    print(
        f"HTTP transcript/job ledger verified for {count} synthetic meetings; audio DB/checksum and approval invariants are separate mandatory checks"
    )

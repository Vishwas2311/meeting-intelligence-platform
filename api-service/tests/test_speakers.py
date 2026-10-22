import pytest


def meeting(client, auth):
    mid = client.post(
        "/api/meetings",
        headers=auth,
        json={
            "title": "Speaker test",
            "consent_confirmed": True,
        },
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    return mid


def test_speaker_name_persists_without_rewriting_transcript(client, auth):
    from app.database import SessionLocal
    from app.models import Mom

    mid = meeting(client, auth)
    speakers = ["live:session-a:Guest-1", "live:session-a:Guest-2", "live:session-b:Guest-1"]
    segments = [
        dict(
            client_event_id=str(i),
            text=f"Turn {i}",
            start_ms=i * 1000,
            end_ms=i * 1000 + 500,
            speaker_id=speaker,
        )
        for i, speaker in enumerate([speakers[0], speakers[1], speakers[0], speakers[2]])
    ]
    assert (
        client.post(
            f"/api/meetings/{mid}/segments", headers=auth, json={"segments": segments}
        ).status_code
        == 200
    )
    before = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]
    # Display names remain editable even when the minutes are approved and immutable.
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, status="approved", data={"summary": "Keep approved text"}))
        db.commit()
    body = {"speaker_id": speakers[0], "display_name": "  Raj  "}
    result = client.patch(f"/api/meetings/{mid}/speakers", headers=auth, json=body)
    assert result.status_code == 200
    assert result.json()["display_name"] == "Raj"
    saved = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()
    assert saved["speaker_names"] == {speakers[0]: "Raj"}
    assert saved["segments"] == before
    assert speakers[2] not in saved["speaker_names"]
    snapshot = client.get(f"/api/meetings/{mid}/snapshot", headers=auth).json()
    assert snapshot["speakerNames"] == saved["speaker_names"]
    assert snapshot["minutes"]["data"]["summary"] == "Keep approved text"
    assert (
        client.post(
            f"/api/meetings/{mid}/segments", headers=auth, json={"segments": segments}
        ).json()["duplicates"]
        == 4
    )
    body["display_name"] = "Raj Kumar"
    assert client.patch(f"/api/meetings/{mid}/speakers", headers=auth, json=body).status_code == 200
    other = meeting(client, auth)
    assert (
        client.get(f"/api/meetings/{other}/transcript", headers=auth).json()["speaker_names"] == {}
    )
    assert (
        client.patch(f"/api/meetings/{other}/speakers", headers=auth, json=body).status_code == 404
    )
    events = client.get(f"/api/meetings/{mid}/audit", headers=auth).json()
    assert sum(e["action"] == "speaker.renamed" for e in events) == 2


@pytest.mark.parametrize("name", ["", "   ", "X" * 101])
def test_speaker_name_validation(client, auth, name):
    mid = meeting(client, auth)
    assert (
        client.patch(
            f"/api/meetings/{mid}/speakers",
            headers=auth,
            json={
                "speaker_id": "live:session:Guest-1",
                "display_name": name,
            },
        ).status_code
        == 422
    )


def test_speaker_rename_requires_meeting_access(client, auth):
    from app.database import SessionLocal
    from app.models import Meeting

    mid = meeting(client, auth)
    with SessionLocal() as db:
        db.get(Meeting, mid).created_by = "another-user"
        db.commit()
    assert (
        client.patch(
            f"/api/meetings/{mid}/speakers",
            headers=auth,
            json={
                "speaker_id": "live:session:Guest-1",
                "display_name": "Raj",
            },
        ).status_code
        == 403
    )

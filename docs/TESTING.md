# Testing the meeting application

Run commands below from the repository root in PowerShell. Current execution results
are recorded separately in `output/testing/TEST-REPORT.md`.

## What each layer checks

| Layer                         | What is exercised                                                                                                                 | External dependencies                                                                                                                       |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend unit and API tests    | Validation, authentication, meeting lifecycle, transcript uploads, speaker names, UTC timestamps, retry decisions                 | FastAPI TestClient and a temporary SQLite database; provider requests are mocked                                                            |
| Backend integration tests     | API to database to worker, minutes generation and approval, audio decoding and recovery, backup/restore, durable retries          | Real application code and files; controlled transcription/LLM responses                                                                     |
| Frontend unit tests           | Speaker labels, reconnect isolation, stopwatch, IndexedDB commit/abort behavior, API and development proxy behavior               | Node test runner; controlled browser/storage objects where needed                                                                           |
| Browser UI and workflow tests | Recording controls, both audio inputs, transcript display, renaming, minutes review, history, offline behavior, responsive layout | Real headless Edge and Vite; requests reach the test FastAPI app through an intercepted transport; microphone/Speech/LLM data are synthetic |
| Live meeting acceptance       | Actual voices, remote audio, echo, interruptions, speaker attribution quality                                                     | Real Teams/Zoom and Azure; perform separately with authorized participants                                                                  |

Automated tests use separate temporary databases and audio directories. They do not
create meetings in the running application's database or record the physical microphone.
No percentage of source-code coverage is claimed by these test counts.

## Run the tests

Backend tests, including API and integration scenarios:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
```

The frontend uses Node. If Node is absent from PATH in this workspace, use the installed
Codex runtime as follows:

```powershell
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) {
  $node = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
}
Push-Location frontend
try {
  & $node --test
  & $node node_modules/typescript/bin/tsc --noEmit
  & $node node_modules/vite/bin/vite.js build
} finally {
  Pop-Location
}
```

For browser tests, keep Vite available at `http://localhost:5173` and ensure Microsoft
Edge is installed. The already-running app supplies Vite locally; the test harness starts
its own isolated test API and worker. Run one browser suite at a time so very short native
audio tests are not competing with another browser run:

```powershell
Remove-Item Env:UI_CASE_FILTER -ErrorAction SilentlyContinue
& $node frontend/e2e/ui.mjs
```

To run only the speaker scenarios:

```powershell
$env:UI_CASE_FILTER = 'speakers:'
try {
  & $node frontend/e2e/ui.mjs
} finally {
  Remove-Item Env:UI_CASE_FILTER -ErrorAction SilentlyContinue
}
```

The filter is a case-sensitive substring of the test name. Each browser invocation
replaces `output/playwright/negative/results.json`; preserve a copy before a focused rerun
if you need the complete suite's original results. Screenshots are saved alongside it.

## Concrete test examples

| Example                 | Action                                                                        | Expected result                                                                         | Existing automated test                                                                                |
| ----------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| API authentication      | Request meetings without credentials                                          | HTTP 401                                                                                | `test_meetings.py::test_auth_and_consent`                                                              |
| Recording authorization | Create a meeting without confirming recording authorization                   | HTTP 422                                                                                | `test_meetings.py::test_auth_and_consent`                                                              |
| Duplicate upload        | Send the same transcript event twice                                          | One stored event; retry acknowledged as a duplicate                                     | `test_meetings.py::test_lifecycle_transcript_idempotency`                                              |
| Conflicting retry       | Retry an event ID with different text                                         | HTTP 409; original text retained                                                        | `test_negative_paths.py::test_conflicting_transcript_retry_cannot_silently_replace_or_hide_text`       |
| Speaker persistence     | Rename one speaker, then read transcript and history snapshot                 | Name saved only for that meeting and speaker; original IDs/text/timestamps unchanged    | `test_speakers.py::test_speaker_name_persists_without_rewriting_transcript`                            |
| Invalid name            | Submit a blank or overlong speaker name                                       | HTTP 422                                                                                | `test_speakers.py::test_speaker_name_validation`                                                       |
| Meeting ownership       | Rename a speaker in someone else's meeting                                    | HTTP 403                                                                                | `test_speakers.py::test_speaker_rename_requires_meeting_access`                                        |
| Timezone conversion     | Load a revision stored at 07:36:27 UTC                                        | Offset present in API; 13:06:27 in India                                                | `test_meetings.py::test_history_timestamps_include_utc_timezone`                                       |
| Audio integrity         | Upload audio with the wrong checksum                                          | HTTP 422; incorrect upload rejected                                                     | `test_meetings.py::test_chunk_integrity`                                                               |
| Recovery integration    | Queue recovery for a verified saved recording and execute the worker          | Decoded audio reaches the controlled provider; recovered text saved; originals retained | `test_architecture.py::test_recovery_decodes_audio_preserves_original_and_clears_only_verified_losses` |
| Speaker UI              | Emit three speaker IDs, rename two, reload and open saved/offline transcripts | Matching names appear across live, history, and transcript evidence                     | Browser test `speakers: three voices can be renamed across live, saved and offline transcripts`        |
| Failed rename UI        | Fail the rename API request                                                   | Error remains in the dialog; old label remains; retry is possible                       | Browser test `speakers: a failed rename retains the old label and works on a phone`                    |
| Reconnect isolation     | Reconnect transcription and receive Guest-1 again                             | Separate identity; no automatic inheritance of the previous person's name               | Browser test `speakers: reconnecting does not assign an earlier name to a new guest ID`                |
| Approval race           | Hold an older refresh while approval succeeds                                 | Screen remains approved; draft editing stays disabled                                   | Browser test `speakers: an older refresh cannot undo approval`                                         |

For example, run the speaker API tests alone:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_speakers.py -v
```

An API sequence exercised by the persistence test is:

```text
POST  /api/meetings                    -> 201, meeting ID
POST  /api/meetings/{id}/start          -> 200
POST  /api/meetings/{id}/segments       -> 200
PATCH /api/meetings/{id}/speakers       -> 200
GET   /api/meetings/{id}/transcript     -> 200, saved name and unchanged segments
GET   /api/meetings/{id}/snapshot       -> 200, same saved speaker-name mapping
```

Example rename request body (use the actual stored speaker ID, not a display label):

```json
{
  "speaker_id": "live:example-session:Guest-1",
  "display_name": "Raj"
}
```

The response returns the saved ID and name. The transcript contains:

```json
{
  "speaker_names": {
    "live:example-session:Guest-1": "Raj"
  }
}
```

These payloads illustrate the contract; the automated fixtures create the meeting and
speaker first. Sending this example ID to an unrelated real meeting returns HTTP 404.

## Live Teams/Zoom acceptance examples

Use a short new meeting and headphones. Select microphone plus shared meeting audio.

| Scenario          | Example conversation/action                                                                             | What to check                                                                                                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two voices        | Person A: "I will review the deployment." Person B: "I will check the database." Person A speaks again. | Both sides are audible; distinct voices get labels; A's later identified turns usually retain A's label within the same session. Early unknown turns can remain unidentified. |
| Three voices      | Each person speaks several full sentences, taking turns.                                                | Three distinct labels appear when Azure distinguishes all three voices. Note misattributions for review; label count alone does not prove accuracy.                           |
| Manual names      | Rename A's label to Raj and B's label to Priya.                                                         | All matching entries update, timestamps stay unchanged, and new turns in the same session use the saved names.                                                                |
| Persistence       | Stop, synchronize, open Meeting History, then open Transcript. Refresh the browser.                     | Transcript, names and timestamps survive reopening.                                                                                                                           |
| Short pause       | One person pauses briefly within a sentence and continues.                                              | Recognition can create another text segment, but a new segment should not itself create a new speaker identity.                                                               |
| Pause/reconnect   | Pause/resume or briefly interrupt transcription.                                                        | New session IDs remain separate. If the same person receives a new label, manually give it the same name; names must not silently transfer to a different voice.              |
| Overlap/echo      | Briefly speak together, then return to clear turn-taking.                                               | Note omitted words, unknown labels and incorrect assignments. Perfect separation is not an acceptance claim for overlapping mixed audio.                                      |
| Audio source ends | End screen/tab sharing while recording.                                                                 | Capture stops or is marked incomplete; incomplete minutes cannot be silently approved.                                                                                        |

The real Azure two-voice sample used during implementation is a useful service smoke
test. It is not a substitute for this meeting-specific check with actual microphones,
remote playback, room acoustics, and participants. Automated suites also do not qualify
PostgreSQL deployment, extended load, every browser, or corporate audio/proxy policies.

# Frontend / streaming integration contract

The browser app needs a **real** capture source; the backend's `/start` is only session orchestration.

1. Authenticate, create a meeting with explicit consent and selected audio source.
2. Request browser `getUserMedia` for microphone or supported `getDisplayMedia` for tab audio; verify real audio tracks. Remote Teams desktop participants are not automatically included.
3. Persist audio chunks from `MediaRecorder` into IndexedDB, including checksums, elapsed/recorded timestamps and upload queue. Acknowledgment from the backend is required to mark a chunk synchronized. `MediaRecorder` format may not match Azure recovery API without conversion.
4. Request `/api/speech/token` through authenticated backend, then configure `microsoft-cognitiveservices-speech-sdk` to use token/region. Refresh token in time during long calls. Note token endpoint requires networking and corporate Azure endpoint configuration.
5. Start `SpeechRecognizer.startContinuousRecognitionAsync`; handle `recognizing` as interim caption and `recognized` as final segment. Persist only finalized segments to `/segments`, using a client-generated stable event id for deduplication.
6. Report capture status separately from STT status to `/capture-status`. Never show "recording" based only on API's `/start` response.
7. On `canceled`, STT interruption or network failure, keep capturing locally **if** source and storage stay healthy. Warn immediately and attempt reconnect. Don't silently drop gaps.
8. On Pause, halt capture, record gap boundaries; on Resume reacquire audio and speech connection; on Stop flush local queue, then stop and generate MoM.
9. On tab closure, browser audio capture stops: only committed chunks survive. Persistent capture beyond tab closure requires an approved desktop agent.
10. Teams native Graph transcripts are a separate optional input; not guaranteed to be available live.

## Example JavaScript SDK event wiring (non-executable sketch)
```ts
recognizer.recognizing = (_sender, event) => setInterimCaption(event.result.text);
recognizer.recognized = async (_sender, event) => {
  if (event.result.reason === SpeechSDK.ResultReason.RecognizedSpeech) {
    await durableQueue.enqueueFinalSegment({
      client_event_id: crypto.randomUUID(),
      text: event.result.text,
      // Actual recognized offsets must be mapped carefully to meeting clock.
      start_ms: meetingClock.offsetFromSpeechTicks(event.result.offset),
      end_ms: meetingClock.offsetFromSpeechTicks(event.result.offset + event.result.duration)
    });
  }
};
recognizer.canceled = () => enterRecordingOnlyAndReconnect();
recognizer.startContinuousRecognitionAsync(onStarted,onError);
```

## Important limits
An Azure live connection is a streaming API, not a Fast Transcription REST upload. A browser must never contain primary/secondary Speech subscription keys. The current implementation provides a backend token broker, but private-network routing and actual SDK integration require testing with the user's tenant and region.

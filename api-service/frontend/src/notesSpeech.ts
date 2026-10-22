import { request } from './api';
import type { SpeechToken } from './types';
import { createTranscriber } from './transcriber';
import { createSpeechPreparation } from './speechPreparation';
import { speechOperation } from './speechOperation';

const prepare = createSpeechPreparation(
  () => import('microsoft-cognitiveservices-speech-sdk'),
  () => request<SpeechToken>('/api/speech/token', 'POST'),
);

/** A capture owns its callbacks, including final phrases delivered during shutdown. */
export function notesSpeech(
  stream: MediaStream,
  language: string,
  callbacks: {
    partial: (text: string) => void;
    final: (text: string) => void;
    state: (state: string) => void;
    failed: () => void;
  },
) {
  let recognizer: ReturnType<typeof createTranscriber> | null = null;
  let stopping = false,
    closed = false;
  let interrupted = false;
  let renewal: ReturnType<typeof setInterval> | undefined;
  const seen = new Set<string>();
  const fail = () => {
    if (!stopping && !closed) {
      interrupted = true;
      callbacks.state('Live transcription unavailable — audio is still recording.');
      callbacks.failed();
    }
  };
  const close = () => {
    closed = true;
    clearInterval(renewal);
    try {
      recognizer?.close();
    } catch {
      /* Audio remains available for recovery. */
    }
  };
  return {
    async start() {
      callbacks.state('Connecting live transcription…');
      try {
        const { speechsdk, credentials } = await prepare();
        if (closed || stopping) return;
        const rec = createTranscriber(speechsdk, credentials, language, stream);
        recognizer = rec;
        rec.transcribing = (_s, event) => {
          if (!closed && !stopping) callbacks.partial(event.result.text || '');
        };
        rec.transcribed = (_s, event) => {
          const result = event.result;
          if (
            closed ||
            result.reason !== speechsdk.ResultReason.RecognizedSpeech ||
            !result.text?.trim()
          )
            return;
          const key = result.resultId || `${result.offset}:${result.duration}:${result.text}`;
          if (seen.has(key)) return;
          seen.add(key);
          callbacks.partial('');
          callbacks.final(result.text.trim());
        };
        rec.canceled = fail;
        rec.sessionStopped = fail;
        await speechOperation((ok, no) => rec.startTranscribingAsync(ok, no), 8000);
        if (closed || stopping || interrupted) return;
        callbacks.state('Live transcription connected');
        renewal = setInterval(
          () => {
            void request<SpeechToken>('/api/speech/token', 'POST')
              .then((token) => {
                if (!closed && !stopping) rec.authorizationToken = token.token;
              })
              .catch(fail);
          },
          8 * 60 * 1000,
        );
      } catch {
        fail();
      }
    },
    async stop() {
      stopping = true;
      clearInterval(renewal);
      try {
        if (recognizer && !closed)
          await speechOperation((ok, no) => recognizer!.stopTranscribingAsync(ok, no), 4000);
      } catch {
        callbacks.failed();
      } finally {
        close();
      }
    },
    close,
  };
}

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
import sdk from 'microsoft-cognitiveservices-speech-sdk';

const source = await readFile(new URL('./src/transcriber.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { createTranscriber, languageLabel } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);
const credentials = {
  token: 'synthetic',
  region: 'centralindia',
  expires_in_seconds: 600,
  auto_detect_languages: ['en-IN', 'hi-IN', 'mr-IN', 'gu-IN'],
};

test('real SDK uses continuous detection with diarization on the v2 endpoint', () => {
  const stream = sdk.AudioInputStream.createPushStream();
  const rec = createTranscriber(sdk, credentials, 'auto', stream);
  try {
    const properties = rec.properties;
    assert.equal(
      properties.getProperty(sdk.PropertyId.SpeechServiceConnection_LanguageIdMode),
      'Continuous',
    );
    assert.equal(
      properties.getProperty(sdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguages),
      credentials.auto_detect_languages.join(','),
    );
    assert.equal(
      properties.getProperty(sdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults),
      'true',
    );
    assert.equal(
      properties.getProperty(sdk.PropertyId.SpeechServiceConnection_Endpoint),
      'wss://centralindia.stt.speech.microsoft.com/speech/universal/v2',
    );
    assert.equal(rec.authorizationToken, 'synthetic');
    assert.equal(languageLabel('auto'), 'Auto-detect');
  } finally {
    rec.close();
    stream.close();
  }
});

test('explicit locales bypass automatic detection and remain unchanged', () => {
  for (const language of ['mr-IN', 'gu-IN', 'hi-IN', 'fr-FR']) {
    const stream = sdk.AudioInputStream.createPushStream();
    const rec = createTranscriber(sdk, credentials, language, stream);
    try {
      assert.equal(
        rec.properties.getProperty(sdk.PropertyId.SpeechServiceConnection_RecoLanguage),
        language,
      );
      assert.equal(
        rec.properties.getProperty(
          sdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguages,
        ),
        undefined,
      );
      assert.equal(languageLabel(language), language);
    } finally {
      rec.close();
      stream.close();
    }
  }
});

test('missing detection configuration fails instead of silently reverting to English', () => {
  const stream = sdk.AudioInputStream.createPushStream();
  try {
    assert.throws(
      () => createTranscriber(sdk, { ...credentials, auto_detect_languages: [] }, 'auto', stream),
      /Automatic language detection is unavailable/,
    );
  } finally {
    stream.close();
  }
});

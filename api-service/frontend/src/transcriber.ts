import type * as SpeechSDK from 'microsoft-cognitiveservices-speech-sdk';
import type { SpeechToken } from './types';

export function automaticLanguageLabels(languages: string[]) {
  const names = new Intl.DisplayNames(['en'], { type: 'language' });
  return {
    label: languages.length
      ? `Auto · ${languages.map((locale) => locale.split('-')[0].toUpperCase()).join(' ')}`
      : 'Auto · loading languages…',
    description: languages.length
      ? languages.map((locale) => names.of(locale.split('-')[0]) || locale).join(', ')
      : 'Language configuration unavailable.',
  };
}

export function languageLabel(language: string) {
  return language === 'auto' ? 'Auto-detect' : language;
}

export function createTranscriber(
  sdk: typeof SpeechSDK,
  credentials: SpeechToken,
  language: string,
  stream: MediaStream,
) {
  const automatic = language === 'auto';
  const languages = credentials.auto_detect_languages;
  if (automatic && (!languages?.length || languages.length > 10)) {
    throw new Error(
      'Automatic language detection is unavailable. Restart the application to update Speech settings.',
    );
  }
  const config = sdk.SpeechConfig.fromAuthorizationToken(credentials.token, credentials.region);
  if (automatic) {
    config.setProperty(
      sdk.PropertyId.SpeechServiceConnection_Endpoint,
      `wss://${credentials.region}.stt.speech.microsoft.com/speech/universal/v2`,
    );
  }
  config.setProperty(sdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults, 'true');
  if (!automatic) config.speechRecognitionLanguage = language;
  const capture = sdk.AudioConfig.fromStreamInput(stream);
  if (!automatic) return new sdk.ConversationTranscriber(config, capture);
  const detection = sdk.AutoDetectSourceLanguageConfig.fromLanguages(languages);
  detection.mode = sdk.LanguageIdMode.Continuous;
  return sdk.ConversationTranscriber.FromConfig(config, detection, capture);
}

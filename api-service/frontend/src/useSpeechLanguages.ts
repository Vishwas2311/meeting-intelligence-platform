import { useEffect, useState } from 'react';
import { request } from './api';
import { automaticLanguageLabels } from './transcriber';

/** Display the same server-owned candidates used by live Speech and recovery. */
export function useSpeechLanguages() {
  const [languages, setLanguages] = useState<string[]>([]);
  useEffect(() => {
    let disposed = false;
    const refresh = () => {
      void request<{ auto_detect_languages: string[] }>('/api/speech/languages')
        .then((value) => {
          if (
            !Array.isArray(value.auto_detect_languages) ||
            !value.auto_detect_languages.every((locale) => typeof locale === 'string')
          )
            return;
          if (!disposed) setLanguages(value.auto_detect_languages);
        })
        .catch(() => {});
    };
    refresh();
    window.addEventListener('focus', refresh);
    return () => {
      disposed = true;
      window.removeEventListener('focus', refresh);
    };
  }, []);
  return automaticLanguageLabels(languages);
}

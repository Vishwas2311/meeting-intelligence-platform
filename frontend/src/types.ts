export interface Meeting {
  id: string;
  title: string;
  language: string;
  status: string;
  capture_status: string;
  stt_status: string;
  sync_status: string;
  source: string;
}

export interface Segment {
  id?: string;
  client_event_id: string;
  text: string;
  start_ms: number;
  end_ms: number;
  speaker_id?: string | null;
  source?: 'live' | 'recovered' | 'imported';
}

export interface Job {
  id: string;
  kind: string;
  status: string;
  attempts: number;
  error: string | null;
}

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface Minutes {
  status: string;
  data: {
    title: string;
    summary: string;
    discussion_points: string[];
    decisions: Record<string, JsonValue>[];
    action_items: Record<string, JsonValue>[];
    risks: string[];
    unanswered_questions: string[];
    transcript_gaps: Record<string, number>[];
    requires_review: boolean;
  };
  approved_by?: string | null;
}

export interface SpeechToken {
  token: string;
  region: string;
  expires_in_seconds: number;
}

export interface Meeting {
  revision?: number;
  id: string;
  title: string;
  language: string;
  status: string;
  capture_status: string;
  stt_status: string;
  sync_status: string;
  source: string;
  created_at?: string;
  started_at?: string | null;
  ended_at?: string | null;
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
  run_after?: string | null;
  lease_until?: string | null;
  completed_sections?: number;
  retry_attempts?: number;
}

export interface CachedMeeting {
  id: string;
  meeting: Meeting;
  segments: Segment[];
  minutes: Minutes | null;
  jobs: Job[];
  cachedAt: string;
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

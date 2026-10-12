export type LinkStatus =
  | 'queued' | 'processing' | 'to_review' | 'error'
  | 'approved' | 'rejected' | 'duplicate';

export interface User {
  id: number;
  name: string;
  antbox_redactor: string | null;
  available: boolean;
  created_at: string;
  deleted_at: string | null;
  todo: number;
  online: boolean;
}

export interface AiDecision {
  value: string;
  confidence: number;
  evidence: string;
  kept: boolean;
  reason: string;
}

export interface LinkInfo {
  author?: string | null;
  published_date?: string | null;
  source?: string | null;
  country?: string | null;
  companies?: string[];
  [k: string]: unknown;
}

export interface Link {
  id: number;
  url: string;
  normalized_url: string;
  final_url: string | null;
  source: string;
  status: LinkStatus;
  assignee_id: number | null;
  assignee_name: string | null;
  title: string | null;
  pdf_file: string | null;
  summary_html: string | null;
  metadata: Record<string, string[]>;
  metadata_ai: Record<string, string[]>;
  info: LinkInfo;
  ai_details: Record<string, AiDecision[]>;
  error: string | null;
  near_duplicate_of: number | null;
  duplicate_of: number | null;
  locked: boolean;
  locked_by: number | null;
  locked_by_name: string | null;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
  approved_by_name: string | null;
  stale: 'warning' | 'critical' | null;
  // detail only
  history?: { action: string; detail: string; at: string; user: string | null }[];
  near_duplicate_of_link?: { id: number; title: string; url: string; status: string } | null;
  duplicate_of_link?: { id: number; title: string; url: string; status: string } | null;
  registry?: { id: number; approved_at: string; operator_name: string }[];
  text_length?: number;
}

export interface LinkPage { total: number; items: Link[]; }

export interface PreviewItem {
  url: string;
  normalized?: string;
  state: 'new' | 'duplicate' | 'invalid' | 'repeat';
  message?: string;
  social?: boolean;
  domain?: string;
  existing?: { id: number; title: string | null; status: string; assignee: string | null };
}

export interface IngestResult {
  created: { id: number; url: string; assignee_id: number | null }[];
  duplicates: { url: string; id: number; title: string | null; status: string; assignee: string | null }[];
  invalid: string[];
}

export interface RefValue { label: string; display: string; id: string; }
export interface RefField {
  name: string; id: string; multi: boolean; ai: boolean; required: boolean; values: RefValue[];
  mode: 'ai' | 'auto' | 'manual' | 'unused';
  source_name: string;
}

export interface Stats {
  my_todo: number;
  my_to_review: number;
  global_queue: number;
  team_todo: number;
  processing: number;
  errors: number;
  done_today_me: number;
  done_today_team: number;
  progress_today_me: number;
  progress_overall: number;
  velocity: { days: string[]; me: number[]; team: number[]; avg_team_per_day: number };
  stale: { warning: number; critical: number; warning_days: number; critical_days: number };
  by_status: Record<string, number>;
  workload: { id: number; name: string; available: number; todo: number; done_7d: number }[];
  ai_acceptance: number | null;
  approved_total: number;
  worker: { pending: number; running: number; failed: number; social_today: number; social_daily_cap: number };
}

export interface RegistryEntry {
  id: number;
  link_id: number;
  approved_at: string;
  operator_name: string;
  title: string;
  url: string;
  metadata: { antbox: Record<string, string[]>; info: LinkInfo };
  summary_text: string;
  pdf_file: string | null;
  csv_file: string | null;
  pdf_sha256: string | null;
  csv_sha256: string | null;
  entry_hash: string;
}

export interface Report {
  id: number;
  title: string;
  period_start: string;
  period_end: string;
  granularity: string;
  link_count: number;
  created_at: string;
  created_by_name: string | null;
  pdf_file: string | null;
  html_file: string | null;
  markdown?: string;
  html?: string;
}

export interface Settings {
  social: {
    enabled: boolean;
    session_present: boolean;
    session_updated_at: string | null;
    domains: string[];
    networks: { name: string; credentials: boolean; auto_2fa: boolean; last_login: { at: string; result: string } | null }[];
  };
  llm: { fake: boolean; configured: boolean; model: string; host: string; confidence_threshold: number; require_evidence: boolean };
  referential: { file: string; fields: number; required: string[] };
  antbox_csv: { column: string; rule: string; referential_field: string | null; values: number }[];
  distribution: { queue_cap_per_user: number };
  notifications: { google_chat: boolean; digest_hour: number };
  csv: { separator: string; multi_separator: string; values: string };
  worker: Stats['worker'];
}

export interface LinkFilters {
  assignee: 'me' | 'all' | 'global' | string;
  state: 'todo' | 'done' | 'all';
  doc_type: string;
  date_from: string;
  date_to: string;
  q: string;
  sort: string;
  order: 'asc' | 'desc';
}

export const STATUS_LABEL: Record<LinkStatus, string> = {
  queued: 'Queued',
  processing: 'Processing',
  to_review: 'To review',
  error: 'Error',
  approved: 'Approved',
  rejected: 'Rejected',
  duplicate: 'Duplicate',
};

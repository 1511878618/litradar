/**
 * Types mirroring the JSON contracts in docs/design/03-contracts.md,
 * docs/design/02-engine.md (§3 JSONL) and docs/design/05-report.md.
 *
 * This file is deliberately declarative: the front-end renders these values, it
 * never derives relevance, date windows or report content from them.
 */

/** Exit codes from docs/design/02-engine.md §2. */
export const EXIT = {
  OK: 0,
  FAIL: 1,
  CONFIG: 2,
  SCREEN: 3,
  ALL_FAILED: 4,
  DOCTOR: 5,
  LOCKED: 6,
  CANCELLED: 7,
} as const;

export const SCHEMA_VERSION = 2;

export type ScanStatus = "ok" | "metadata-only" | "blocked" | "timeout" | "error";
export type DateSource = "publisher" | "crossref" | "manual" | "missing";
export type Visibility = "publisher" | "metadata-only";

export interface WindowInfo {
  kind: string;
  start: string;
  end: string;
  tz: string;
  label: string;
}

/** One article as persisted in store/<run_id>.json. */
export interface Article {
  id: string;
  title: string;
  journal: string;
  url: string;
  surface?: string | null;
  doi?: string | null;
  online_date?: string | null;
  date_source?: DateSource;
  abstract_raw?: string | null;
  abstract_source?: string | null;
  authors?: string[];
  content_type?: string;
  first_seen_at?: string | null;
  /** Only present in store items: already reported in an earlier digest. */
  already_seen?: boolean;
  extra?: Record<string, unknown>;
}

/** One journal's coverage record inside store/<run_id>.json. */
export interface JournalScan {
  journal: string;
  status: ScanStatus;
  surface?: string | null;
  url?: string | null;
  http?: number | null;
  items_seen?: number;
  reason?: string | null;
  scanned_at?: string | null;
  visibility?: Visibility | string;
}

export interface StorePayload {
  schema?: number;
  run_id: string;
  engine?: string;
  scan_at?: string;
  window?: WindowInfo;
  journals?: JournalScan[];
  items?: Article[];
  date_unverified?: Article[];
  out_of_window?: Article[];
  excluded_type?: Article[];
}

/** runs/<run_id>/screen.json (LLM output). */
export interface ScreenResult {
  id: string;
  relevant?: boolean;
  relevance?: number;
  why?: string;
  topic_tags?: string[];
  abstract_en?: string;
  abstract_mode?: "verbatim" | "faithful-summary" | "none" | string;
  conclusion?: string;
  conclusion_source?: string;
}

export interface ScreenPayload {
  run_id?: string;
  model?: string;
  screening?: string;
  results?: ScreenResult[];
}

/** runs/<run_id>/scores.json (embedding output). Only affects ordering. */
export interface ScoresPayload {
  schema?: number;
  computed_at?: string;
  model?: string;
  profile?: { source?: string; n_items?: number };
  scores?: Record<string, number>;
}

/** state/journal_scans.json: last attempt plus history counters. */
export interface ScanState extends JournalScan {
  last_ok_at?: string | null;
  last_attempt_at?: string | null;
  consecutive_failures?: number;
}

export type ScanStateMap = Record<string, ScanState>;

/** state/seen.json is only ever read (and only for a count in the footer). */
export interface SeenEntry {
  title?: string;
  journal?: string;
  doi?: string | null;
  url?: string;
  first_seen_at?: string;
  last_reported_at?: string;
  last_run?: string;
}

export type SeenMap = Record<string, SeenEntry>;

/** `litradar --version --json` */
export interface VersionPayload {
  engine?: string;
  schema?: number;
}

/** `litradar doctor --json` */
export interface DoctorCheck {
  name: string;
  ok: boolean;
  detail?: string;
  severity?: string;
}

export interface DoctorPayload {
  ok?: boolean;
  summary?: string;
  checks?: DoctorCheck[];
}

/**
 * A line on the `--jsonl` stream. Kept open-ended on purpose: unknown events
 * must be ignored rather than crashing the panel.
 */
export interface RunEvent {
  event: string;
  [key: string]: unknown;
}

/** Front-matter anchors from docs/design/05-report.md §6. */
export interface ReportMeta {
  title?: string;
  date?: string;
  coverage?: string;
  screening?: string;
  new_items?: number;
  highlights?: number;
  window?: string;
  journals?: string[];
  validationLines: string[];
  hasValidation: boolean;
}

export type ActionKind = "fetch" | "render" | "embed" | "doctor";

export type OutcomeKind =
  | "ok"
  | "error"
  | "config"
  | "screen"
  | "all-failed"
  | "doctor"
  | "locked"
  | "cancelled"
  | "enoent"
  | "engine-missing";

/** Live state while a child process is running. Survives view close/open. */
export interface JobState {
  action: ActionKind;
  startedAt: number;
  runId?: string;
  phase: string;
  progress?: { stage: string; done: number; total: number };
  scans: Record<string, LiveScan>;
  reportPath?: string;
  coverage?: string;
  items?: number;
  highlights?: number;
  cancelRequested: boolean;
  stderrTail: string;
}

export interface LiveScan {
  status: ScanStatus | "running";
  http?: number | null;
  items?: number;
  reason?: string | null;
}

/** Outcome of the most recent finished job. Rendered by the panel. */
export interface JobOutcome {
  kind: OutcomeKind;
  action: ActionKind;
  code: number | null;
  message: string;
  detail?: string;
  stderrTail: string;
  reportPath?: string;
  doctor?: DoctorPayload;
  finishedAt: number;
}

export type EngineState = "unknown" | "checking" | "ok" | "below-min" | "missing" | "error";

export interface EngineStatus {
  state: EngineState;
  engine?: string;
  schema?: number;
  interpreter: string;
  detail?: string;
  checkedAt?: number;
}

export interface LitradarSettings {
  /** Absolute path to the Python interpreter. Never resolved through PATH. */
  pythonPath: string;
  /** Engine working directory. Empty = the current vault root. */
  vaultRoot: string;
  /** Relative to the vault root. Default `.litradar`. */
  storeDir: string;
  /** Name of the environment variable holding the API key (never the key). */
  apiKeyEnv: string;
  /** Panel polling interval in seconds; 0 = off. */
  autoRefreshSeconds: number;
  /** Show the last 20 stderr lines in the panel. */
  showLogTail: boolean;
  /** Minimum engine version accepted by this plugin. */
  minEngine: string;
}

export const DEFAULT_SETTINGS: LitradarSettings = {
  pythonPath: "",
  vaultRoot: "",
  storeDir: ".litradar",
  apiKeyEnv: "LITRADAR_API_KEY",
  autoRefreshSeconds: 30,
  showLogTail: true,
  minEngine: "2.0.0",
};

/** Snapshot of one `.litradar` read. Never written back. */
export interface PanelData {
  initialized: boolean;
  storeDir: string;
  runIds: string[];
  activeRunId: string | null;
  store: StorePayload | null;
  scans: ScanStateMap;
  screen: ScreenPayload | null;
  scores: ScoresPayload | null;
  report: ReportMeta | null;
  reportText: string | null;
  runLogTail: string | null;
  today: string;
  fetchedToday: boolean;
  warnings: string[];
  error?: string;
}

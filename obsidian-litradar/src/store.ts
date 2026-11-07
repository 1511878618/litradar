/**
 * Read-only access to `<vault>/.litradar`.
 *
 * `.litradar` is a dot-directory, so `vault.getMarkdownFiles()` cannot see it.
 * Everything here goes through the low-level `DataAdapter` (`read`/`list`/
 * `exists`/`stat`). There is intentionally no write/modify/create call in this
 * file: the front-end never touches store/, state/seen.json or report files.
 */

import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { App, FileSystemAdapter, TFile } from "obsidian";
import type {
  Article,
  LlmInfo,
  PanelData,
  ProfileInfo,
  ReportMeta,
  ScanStateMap,
  ScheduleInfo,
  ScoresPayload,
  ScreenPayload,
  StorePayload,
} from "./types";

function normalizeRelative(path: string): string {
  return path
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/\/+$/, "");
}

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] ?? path;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** Parse the small, stable front-matter anchors from docs/design/05-report.md §6. */
export function parseFrontmatter(text: string): Record<string, string | string[]> {
  const result: Record<string, string | string[]> = {};
  const normalized = text.replace(/\r\n/g, "\n");
  if (!normalized.startsWith("---")) return result;
  const end = normalized.indexOf("\n---", 3);
  if (end < 0) return result;
  const block = normalized.slice(3, end);
  for (const rawLine of block.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const colon = line.indexOf(":");
    if (colon <= 0) continue;
    const key = line.slice(0, colon).trim();
    let value = line.slice(colon + 1).trim();
    if (!key) continue;
    if (value.startsWith("[") && value.endsWith("]")) {
      result[key] = value
        .slice(1, -1)
        .split(",")
        .map((part) => part.trim().replace(/^["']|["']$/g, ""))
        .filter(Boolean);
      continue;
    }
    value = value.replace(/^["']|["']$/g, "");
    result[key] = value;
  }
  return result;
}

/** `## 校验记录` is the only report section this front-end parses. */
export function extractValidationLines(text: string): string[] {
  const normalized = text.replace(/\r\n/g, "\n");
  const heading = normalized.indexOf("## 校验记录");
  if (heading < 0) return [];
  const rest = normalized.slice(heading + "## 校验记录".length);
  const nextHeading = rest.search(/\n##\s/);
  const body = nextHeading >= 0 ? rest.slice(0, nextHeading) : rest;
  return body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith("*由 litradar"));
}

function mapDateSourceNote(source: string | undefined): string {
  if (source === "crossref") return "（元数据日期，未在官网核实）";
  return "";
}

/** Presentation-only helper used by the panel and its date labels. */
export function dateSourceNote(source: string | undefined): string {
  return mapDateSourceNote(source);
}

interface ScheduleConfig {
  kind: string;
  time: string;
  tz: string;
}

const DEFAULT_SCHEDULE: ScheduleConfig = {
  kind: "daily",
  time: "09:00",
  tz: "Asia/Shanghai",
};

/** Parse the small, stable `schedule:` block from config.yaml / config.local.yaml. */
export function parseScheduleConfig(text: string): Partial<ScheduleConfig> {
  const result: Partial<ScheduleConfig> = {};
  let inSchedule = false;
  for (const rawLine of text.replace(/\r\n/g, "\n").split("\n")) {
    if (/^schedule:\s*(?:#.*)?$/.test(rawLine)) {
      inSchedule = true;
      continue;
    }
    if (!inSchedule) continue;
    if (/^\S/.test(rawLine)) {
      inSchedule = false;
      continue;
    }
    const match = rawLine.match(/^\s+(kind|time|tz):\s*(?:"([^"]*)"|'([^']*)'|([^#\s]+))/);
    if (!match) continue;
    const value = match[2] ?? match[3] ?? match[4] ?? "";
    if (value) result[match[1] as keyof ScheduleConfig] = value;
  }
  return result;
}

function nextRunAt(time: string, now = new Date()): string | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(time.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  const candidate = new Date(now);
  candidate.setHours(hour, minute, 0, 0);
  if (candidate.getTime() <= now.getTime()) candidate.setDate(candidate.getDate() + 1);
  return candidate.toISOString();
}

function launchAgentInstalled(): boolean {
  if (process.platform !== "darwin") return false;
  try {
    return existsSync(join(homedir(), "Library", "LaunchAgents", "com.litradar.daily.plist"));
  } catch {
    return false;
  }
}

function unquoteYaml(value: string): string {
  const text = value.trim();
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    return text.slice(1, -1);
  }
  return text.replace(/\s+#.*$/, "").trim();
}

/** The indented body of a top-level YAML section, or the whole text. */
function yamlBlock(text: string, section?: string): string {
  if (!section) return text;
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const start = lines.findIndex((line) => new RegExp(`^${section}:\\s*(?:#.*)?$`).test(line));
  if (start < 0) return "";
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\S/.test(lines[index])) {
      end = index;
      break;
    }
  }
  return lines.slice(start + 1, end).join("\n");
}

function yamlScalar(text: string, key: string): string {
  const match = text.match(
    new RegExp(`^\\s*${key}:\\s*(?:"([^"]*)"|'([^']*)'|([^#\\n]*))\\s*$`, "m"),
  );
  if (!match) return "";
  return unquoteYaml(match[1] ?? match[2] ?? match[3] ?? "");
}

function yamlList(text: string, key: string): string[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const start = lines.findIndex((line) => new RegExp(`^\\s*${key}:\\s*(?:#.*)?$`).test(line));
  if (start < 0) return [];
  const out: string[] = [];
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) continue;
    const match = line.match(/^\s*-\s*(.+)$/);
    if (!match) break;
    const value = unquoteYaml(match[1]);
    if (value) out.push(value);
  }
  return out;
}

export class LitradarStore {
  constructor(
    private readonly app: App,
    private readonly storeDirSetting: string,
  ) {}

  /** Absolute vault root; needed as the engine's `cwd` and `--vault`. */
  get vaultRoot(): string {
    try {
      const adapter = this.app.vault.adapter;
      if (adapter instanceof FileSystemAdapter) return adapter.getBasePath();
      const maybe = adapter as unknown as { getBasePath?: () => string };
      return typeof maybe.getBasePath === "function" ? maybe.getBasePath() : "";
    } catch {
      return "";
    }
  }

  /** Vault-relative, forward-slash path to the engine root. */
  get storeDir(): string {
    const raw = (this.storeDirSetting || ".litradar").trim();
    let normalized = normalizeRelative(raw);
    const base = normalizeRelative(this.vaultRoot);
    if (normalized.startsWith("/") && base && normalized.startsWith(`${base}/`)) {
      normalized = normalized.slice(base.length + 1);
    }
    return normalized || ".litradar";
  }

  path(...parts: string[]): string {
    return normalizeRelative([this.storeDir, ...parts].join("/"));
  }

  private async adapterExists(path: string): Promise<boolean> {
    try {
      return await this.app.vault.adapter.exists(path);
    } catch {
      return false;
    }
  }

  async exists(path: string): Promise<boolean> {
    return this.adapterExists(normalizeRelative(path));
  }

  async readText(path: string): Promise<string | null> {
    const normalized = normalizeRelative(path);
    if (!(await this.adapterExists(normalized))) return null;
    try {
      return await this.app.vault.adapter.read(normalized);
    } catch {
      return null;
    }
  }

  async readJson<T>(path: string, warnings?: string[]): Promise<T | null> {
    const normalized = normalizeRelative(path);
    if (!(await this.adapterExists(normalized))) return null;
    try {
      const raw = await this.app.vault.adapter.read(normalized);
      return JSON.parse(raw) as T;
    } catch (error) {
      warnings?.push(`读取 ${normalized} 失败：${error instanceof Error ? error.message : String(error)}`);
      return null;
    }
  }

  /** Returns file names (not full paths) inside `dir`, sorted by mtime then name. */
  async listFiles(dir: string): Promise<string[]> {
    const normalized = normalizeRelative(dir);
    if (!(await this.adapterExists(normalized))) return [];
    try {
      const listing = await this.app.vault.adapter.list(normalized);
      return listing.files.map((file) => basename(file));
    } catch {
      return [];
    }
  }

  async isInitialized(): Promise<boolean> {
    return (
      (await this.adapterExists(this.path("config.yaml"))) ||
      (await this.adapterExists(this.path("store"))) ||
      (await this.adapterExists(this.storeDir))
    );
  }

  /** Newest run first (mtime), so a range run_id sorts correctly too. */
  async listRunIds(): Promise<string[]> {
    const names = await this.listFiles(this.path("store"));
    const candidates = names
      .filter((name) => name.toLowerCase().endsWith(".json") && !name.startsWith("."))
      .map((name) => name.slice(0, -".json".length));
    if (candidates.length === 0) return [];

    const withTime = await Promise.all(
      candidates.map(async (id) => {
        let mtime = 0;
        try {
          const stat = await this.app.vault.adapter.stat(this.path("store", `${id}.json`));
          mtime = stat?.mtime ?? 0;
        } catch {
          mtime = 0;
        }
        return { id, mtime };
      }),
    );
    withTime.sort((left, right) => left.mtime - right.mtime || left.id.localeCompare(right.id));
    return withTime.map((entry) => entry.id);
  }

  async readStore(runId: string, warnings?: string[]): Promise<StorePayload | null> {
    const payload = await this.readJson<StorePayload>(this.path("store", `${runId}.json`), warnings);
    if (!isRecord(payload)) return null;
    const presentation = await this.readJson<{titles?: Record<string,string>; summaries?: Record<string,string>; why?: Record<string,string>; extras?: Record<string, Record<string,unknown>>}>(this.path("runs", runId, "presentation.json"));
    for (const item of payload.items || []) {
      item.title_zh = presentation?.titles?.[item.id];
      item.summary_zh = presentation?.summaries?.[item.id];
      item.why_zh = presentation?.why?.[item.id];
      item.extra = {...item.extra, ...presentation?.extras?.[item.id]};
    }
    return payload;
  }

  async readArchive(days: number): Promise<Article[]> {
    const ids = (await this.listRunIds()).filter(id => /^\d{4}-\d{2}-\d{2}$/.test(id)).sort().reverse();
    const cutoff = days ? Date.now() - days * 86400000 : 0;
    const byKey = new Map<string, Article>();
    const versions = await this.readJson<Record<string, unknown[]>>(this.path("state", "versions.json"));
    for (const id of ids) {
      if (days && Date.parse(id + "T23:59:59") < cutoff) continue;
      const store = await this.readStore(id);
      for (const item of store?.items || []) {
        const key = (item.doi || item.id).toLowerCase();
        const previous = byKey.get(key);
        if (previous) {
          (previous.extra!.report_dates as string[]).push(id);
        } else {
          byKey.set(key, {...item, extra: {...item.extra, report_date: id, report_dates: [id],
            versions: versions?.[(item.doi || "").toLowerCase()] || item.extra?.versions}});
        }
      }
    }
    return [...byKey.values()];
  }

  async readScreen(runId: string, warnings?: string[]): Promise<ScreenPayload | null> {
    const payload = await this.readJson<ScreenPayload>(
      this.path("runs", runId, "screen.json"),
      warnings,
    );
    return isRecord(payload) ? payload : null;
  }

  async readScores(runId: string, warnings?: string[]): Promise<ScoresPayload | null> {
    const payload = await this.readJson<ScoresPayload>(
      this.path("runs", runId, "scores.json"),
      warnings,
    );
    return isRecord(payload) ? payload : null;
  }

  async readScans(warnings?: string[]): Promise<ScanStateMap> {
    const payload = await this.readJson<ScanStateMap>(
      this.path("state", "journal_scans.json"),
      warnings,
    );
    return isRecord(payload) ? payload : {};
  }

  async readReport(runId: string, warnings?: string[]): Promise<{ meta: ReportMeta; text: string } | null> {
    const text = await this.readText(this.path("runs", runId, "report.md"));
    if (text === null) {
      if (warnings) {
        // A missing report is normal before the first render; do not warn.
      }
      return null;
    }
    const front = parseFrontmatter(text);
    const validationLines = extractValidationLines(text);
    return {
      text,
      meta: {
        title: typeof front.title === "string" ? front.title : undefined,
        date: typeof front.date === "string" ? front.date : undefined,
        coverage: typeof front.coverage === "string" ? front.coverage : undefined,
        screening: typeof front.screening === "string" ? front.screening : undefined,
        new_items: typeof front.new_items === "string" ? Number(front.new_items) : undefined,
        highlights: typeof front.highlights === "string" ? Number(front.highlights) : undefined,
        window: typeof front.window === "string" ? front.window : undefined,
        journals: Array.isArray(front.journals) ? front.journals : undefined,
        validationLines,
        hasValidation: validationLines.length > 0,
      },
    };
  }

  async readRunLogTail(runId: string, lines = 20): Promise<string | null> {
    const text = await this.readText(this.path("runs", runId, "run.log"));
    if (text === null) return null;
    return text
      .replace(/\r\n/g, "\n")
      .split("\n")
      .filter((line, index, all) => line.length > 0 || index < all.length - 1)
      .slice(-lines)
      .join("\n");
  }

  /**
   * Candidate report locations, in priority order:
   *  1. the path the engine reported in `report_written`;
   *  2. the default template from docs/design/05-report.md §3;
   *  3. the run copy under `.litradar/runs/<run_id>/report.md`.
   */
  reportCandidates(runId: string, windowEnd: string | undefined, reportedPath?: string): string[] {
    const candidates: string[] = [];
    if (reportedPath) candidates.push(normalizeRelative(reportedPath));
    if (windowEnd && /^\d{4}-\d{2}-\d{2}$/.test(windowEnd)) {
      const [year, month] = windowEnd.split("-");
      candidates.push(`02-Reading/文献雷达/${year}/${month}/${windowEnd}.md`);
    }
    candidates.push(this.path("runs", runId, "report.md"));
    return Array.from(new Set(candidates));
  }

  async findReport(
    runId: string,
    windowEnd: string | undefined,
    reportedPath?: string,
  ): Promise<string | null> {
    for (const candidate of this.reportCandidates(runId, windowEnd, reportedPath)) {
      if (await this.adapterExists(candidate)) return candidate;
    }
    return null;
  }

  /** The visible report path, only if Obsidian indexed it as a TFile. */
  getIndexedFile(path: string): TFile | null {
    const file = this.app.vault.getAbstractFileByPath(normalizeRelative(path));
    return file instanceof TFile ? file : null;
  }

  /** Current research profile.  profile.yaml wins; config.yaml is the fallback. */
  async readProfile(): Promise<ProfileInfo> {
    const profileText = (await this.readText(this.path("profile.yaml"))) ?? "";
    if (profileText.trim()) {
      return {
        direction: yamlScalar(profileText, "direction"),
        keywords: yamlList(profileText, "keywords"),
        sources: yamlList(profileText, "sources"),
        model: yamlScalar(profileText, "model"),
        updatedAt: yamlScalar(profileText, "created_at") || null,
        fromZotero: true,
      };
    }
    const configText = (await this.readText(this.path("config.yaml"))) ?? "";
    return {
      direction: yamlScalar(configText, "direction"),
      keywords: yamlList(yamlBlock(configText, "screening"), "keywords"),
      sources: [],
      model: "",
      updatedAt: null,
      fromZotero: false,
    };
  }

  /** Effective model + base URL; config.local.yaml overrides config.yaml. */
  async readLlm(): Promise<LlmInfo> {
    const baseText = (await this.readText(this.path("config.yaml"))) ?? "";
    const localText = (await this.readText(this.path("config.local.yaml"))) ?? "";
    const baseBlock = yamlBlock(baseText, "llm");
    const localBlock = yamlBlock(localText, "llm");
    return {
      model: yamlScalar(localBlock, "model") || yamlScalar(baseBlock, "model") || "deepseek-chat",
      baseUrl:
        yamlScalar(localBlock, "base_url") ||
        yamlScalar(baseBlock, "base_url") ||
        "https://api.deepseek.com",
    };
  }

  /** Schedule + freshness card data.  Reads config only; never writes it. */
  async readSchedule(today: string, store: StorePayload | null): Promise<ScheduleInfo> {
    const baseText = (await this.readText(this.path("config.yaml"))) ?? "";
    const localText = (await this.readText(this.path("config.local.yaml"))) ?? "";
    const config: ScheduleConfig = {
      ...DEFAULT_SCHEDULE,
      ...parseScheduleConfig(baseText),
      ...parseScheduleConfig(localText),
    };
    const lastScanAt = typeof store?.scan_at === "string" ? store.scan_at : null;
    return {
      ...config,
      installed: launchAgentInstalled(),
      nextRunAt: nextRunAt(config.time),
      lastScanAt,
      todayFetched: !!lastScanAt && lastScanAt.slice(0, 10) === today,
    };
  }

  /**
   * Load the whole read-only snapshot. Each file is optional; a malformed file
   * produces a warning instead of a blank panel.
   */
  async loadPanelData(today: string, preferredRunId?: string | null): Promise<PanelData> {
    const warnings: string[] = [];
    const initialized = await this.isInitialized();
    const runIds = initialized ? await this.listRunIds() : [];
    let activeRunId: string | null = null;
    if (preferredRunId && runIds.includes(preferredRunId)) {
      activeRunId = preferredRunId;
    } else if (runIds.length > 0) {
      activeRunId = runIds[runIds.length - 1];
    }

    let store: StorePayload | null = null;
    let scans: ScanStateMap = {};
    let screen: ScreenPayload | null = null;
    let scores: ScoresPayload | null = null;
    let report: ReportMeta | null = null;
    let reportText: string | null = null;
    let runLogTail: string | null = null;

    if (initialized) {
      scans = await this.readScans(warnings);
      if (activeRunId) {
        [store, screen, scores] = await Promise.all([
          this.readStore(activeRunId, warnings),
          this.readScreen(activeRunId, warnings),
          this.readScores(activeRunId, warnings),
        ]);
        const reportRead = await this.readReport(activeRunId, warnings);
        if (reportRead) {
          report = reportRead.meta;
          reportText = reportRead.text;
        }
        runLogTail = await this.readRunLogTail(activeRunId);
      }
    }

    let fetchedToday = false;
    if (store && typeof store.scan_at === "string" && store.scan_at) {
      const scanDate = store.scan_at.slice(0, 10);
      fetchedToday = scanDate === today;
    }
    const schedule = await this.readSchedule(today, store);
    const [profile, llm] = await Promise.all([this.readProfile(), this.readLlm()]);

    return {
      initialized,
      storeDir: this.storeDir,
      runIds,
      activeRunId,
      store,
      scans,
      screen,
      scores,
      report,
      reportText,
      runLogTail,
      today,
      fetchedToday,
      schedule,
      profile,
      llm,
      warnings,
    };
  }
}

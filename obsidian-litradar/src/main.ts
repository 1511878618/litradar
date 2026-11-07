/**
 * Litradar — Obsidian front-end for the local Python engine.
 *
 * This plugin is a *thin* front-end. It displays what the engine wrote under
 * `.litradar`, and it can ask the engine to fetch/render/embed. It never writes
 * engine files, never edits seen.json, never computes date windows, relevance or
 * Markdown, and never installs/upgrades anything.
 */

import { FileSystemAdapter, Notice, Plugin, WorkspaceLeaf } from "obsidian";
import { findReadingEntry, normalizeLibrary, type ReadingStatus, type ZoteroReadingEntry, type ZoteroCollection, type ReadingListStatusOption } from "./reading";
import type { Article } from "./types";
import { Bridge, BridgeError } from "./bridge";
import { LitradarSettingTab } from "./settings";
import { LitradarStore } from "./store";
import { LitradarView, VIEW_TYPE_LITRADAR } from "./view";
import {
  DEFAULT_SETTINGS,
  EXIT,
  type ActionKind,
  type DoctorPayload,
  type EngineStatus,
  type JobOutcome,
  type JobState,
  type LitradarSettings,
  type OutcomeKind,
  type RunEvent,
  type StorePayload,
  type VersionPayload,
} from "./types";

export const PLUGIN_VERSION = "0.4.0";

const ACTION_LABELS: Record<ActionKind, string> = {
  run: "抓取并生成",
  fetch: "只抓取",
  render: "重新生成日报",
  embed: "重算相关度",
  doctor: "引擎自检",
  schedule: "安装定时任务",
  repair: "重试失败期刊",
  maintain: "补齐漏抓与迟到记录",
};

/** Budgets from docs/design/02-engine.md §6, with a little headroom. */
const TIMEOUTS: Record<ActionKind, number> = {
  run: 17 * 60 * 1000,
  fetch: 11 * 60 * 1000,
  render: 6 * 60 * 1000,
  embed: 6 * 60 * 1000,
  doctor: 2 * 60 * 1000,
  schedule: 60 * 1000,
  repair: 20 * 60 * 1000,
  maintain: 60 * 60 * 1000,
};

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** Numeric semver comparison; prerelease/build suffixes are ignored. */
export function compareVersions(current: string, required: string): number {
  const parse = (value: string): number[] =>
    value
      .trim()
      .replace(/^v/i, "")
      .split(/[.+-]/)
      .slice(0, 3)
      .map((part) => {
        const parsed = Number.parseInt(part, 10);
        return Number.isFinite(parsed) ? parsed : 0;
      });
  const left = parse(current);
  const right = parse(required);
  for (let index = 0; index < 3; index += 1) {
    const a = left[index] ?? 0;
    const b = right[index] ?? 0;
    if (a !== b) return a < b ? -1 : 1;
  }
  return 0;
}

export function tailLines(text: string, count = 20): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  while (lines.length > 0 && lines[lines.length - 1].trim() === "") lines.pop();
  return lines.slice(-count).join("\n");
}

export default class LitradarPlugin extends Plugin {
  settings: LitradarSettings = { ...DEFAULT_SETTINGS };
  engine: EngineStatus = { state: "unknown", interpreter: "" };
  engineChecking = false;
  job: JobState | null = null;
  lastOutcome: JobOutcome | null = null;
  doctor: DoctorPayload | null = null;
  reader!: LitradarStore;

  private bridge!: Bridge;
  private listeners = new Set<() => void>();
  private checkTimer: number | null = null;
  private autoTimer: number | null = null;
  private autoStartTimer: number | null = null;
  private autoRunDay: string | null = null;
  private checkToken = 0;
  private bridgeDirty = false;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.reader = new LitradarStore(this.app, this.settings.storeDir);
    this.engine = { state: "unknown", interpreter: this.settings.pythonPath };
    this.makeBridge();

    this.registerView(VIEW_TYPE_LITRADAR, (leaf) => new LitradarView(leaf, this));
    this.addRibbonIcon("radar", "文献雷达", () => {
      void this.activateView();
    });

    this.addCommand({
      id: "litradar-open",
      name: "打开面板",
      callback: () => {
        void this.activateView();
      },
    });
    this.addCommand({
      id: "litradar-run",
      name: "抓取并生成今日日报",
      callback: () => {
        void this.runAction("run");
      },
    });
    this.addCommand({
      id: "litradar-fetch",
      name: "只抓取（不生成日报）",
      callback: () => {
        void this.runAction("fetch");
      },
    });
    this.addCommand({
      id: "litradar-render",
      name: "重新生成日报",
      callback: () => {
        void this.runAction("render");
      },
    });
    this.addCommand({
      id: "litradar-embed",
      name: "重算相关度",
      callback: () => {
        void this.runAction("embed");
      },
    });
    this.addCommand({
      id: "litradar-doctor",
      name: "引擎自检",
      callback: () => {
        void this.runDoctor();
      },
    });
    this.addCommand({
      id: "litradar-open-report",
      name: "打开今日日报",
      callback: () => {
        void this.openReport();
      },
    });

    this.addSettingTab(new LitradarSettingTab(this.app, this));

    this.app.workspace.onLayoutReady(() => {
      void this.checkEngine(false);
      this.startAutoRun();
    });
  }

  onunload(): void {
    if (this.checkTimer !== null) {
      window.clearTimeout(this.checkTimer);
      this.checkTimer = null;
    }
    this.stopAutoRun();
    // Do not leave a python process behind when the plugin is disabled.
    if (this.job) this.bridge?.kill();
    for (const bridge of this.zoteroBridges) bridge.kill();
    this.app.workspace.detachLeavesOfType(VIEW_TYPE_LITRADAR);
  }

  // ------------------------------------------------------------------ settings

  private async loadSettings(): Promise<void> {
    const stored = (await this.loadData()) as Partial<LitradarSettings> | null;
    const merged: LitradarSettings = { ...DEFAULT_SETTINGS, ...(stored ?? {}) };
    merged.pythonPath = typeof merged.pythonPath === "string" ? merged.pythonPath : "";
    merged.vaultRoot = typeof merged.vaultRoot === "string" ? merged.vaultRoot : "";
    merged.storeDir =
      typeof merged.storeDir === "string" && merged.storeDir.trim()
        ? merged.storeDir.trim()
        : DEFAULT_SETTINGS.storeDir;
    merged.apiKeyEnv =
      typeof merged.apiKeyEnv === "string" && merged.apiKeyEnv.trim()
        ? merged.apiKeyEnv.trim()
        : DEFAULT_SETTINGS.apiKeyEnv;
    const seconds = Number(merged.autoRefreshSeconds);
    merged.autoRefreshSeconds = Number.isFinite(seconds) ? clamp(Math.round(seconds), 0, 3600) : 30;
    merged.showLogTail = merged.showLogTail !== false;
    merged.autoRunEnabled = merged.autoRunEnabled !== false;
    merged.minEngine =
      typeof merged.minEngine === "string" && merged.minEngine.trim()
        ? merged.minEngine.trim()
        : DEFAULT_SETTINGS.minEngine;
    merged.profileCollections = Array.isArray(merged.profileCollections)
      ? merged.profileCollections.filter((name): name is string => typeof name === "string")
      : [];
    merged.profileName =
      typeof merged.profileName === "string" ? merged.profileName.trim() : "";
    merged.zoteroTargetCollection = typeof merged.zoteroTargetCollection === "string" ? merged.zoteroTargetCollection : "";
    merged.readingListTodoStatus = typeof merged.readingListTodoStatus === "string" ? merged.readingListTodoStatus : "To Read";
    merged.readingListReadStatus = typeof merged.readingListReadStatus === "string" ? merged.readingListReadStatus : "Read";
    merged.readingLibrary = normalizeLibrary(merged.readingLibrary);
    this.settings = merged;
  }

  zoteroLibrary: ZoteroReadingEntry[] = [];
  zoteroStatuses: ReadingListStatusOption[] = [];
  zoteroLegacyCount = 0;
  zoteroError = "";
  private zoteroLoadedAt = 0;
  private zoteroRefresh: Promise<void> | null = null;
  private zoteroBridges = new Set<Bridge>();
  private zoteroMutations: Promise<void> = Promise.resolve();
  private settingsWrite: Promise<void> = Promise.resolve();

  private persistSettings(update: () => LitradarSettings): Promise<void> {
    const write = this.settingsWrite.catch(() => {}).then(async () => {
      const next = update();
      await this.saveData(next);
      this.settings = next;
    });
    this.settingsWrite = write;
    return write;
  }

  private async auxiliaryProbe(args: string[], timeout: number) {
    const bridge = new Bridge({ pythonPath: this.settings.pythonPath, vaultRoot: this.resolvedVaultRoot });
    this.zoteroBridges.add(bridge);
    try { return await bridge.probeJson(args, timeout); }
    finally { this.zoteroBridges.delete(bridge); }
  }

  private async zoteroCommand(args: string[], timeout = 20000): Promise<unknown> {
    const bridge = new Bridge({ pythonPath: this.settings.pythonPath, vaultRoot: this.resolvedVaultRoot });
    this.zoteroBridges.add(bridge);
    try {
      const result = await bridge.probeJson(["zotero", "reading", ...args, "--json"], timeout);
      if (result.code !== 0 || !result.json) throw new Error(tailLines(result.stderr || result.stdout, 3) || "Zotero 没有返回结果");
      return result.json;
    } finally { this.zoteroBridges.delete(bridge); }
  }

  refreshZoteroReading(force = false): Promise<void> {
    if (this.zoteroRefresh) return force
      ? this.zoteroRefresh.then(() => this.refreshZoteroReading(true))
      : this.zoteroRefresh;
    if (!force && Date.now() - this.zoteroLoadedAt < 30000) return Promise.resolve();
    this.zoteroRefresh = (async () => {
      try {
        const result = await this.zoteroCommand(["--action", "list"]) as { items: ZoteroReadingEntry[]; statuses: ReadingListStatusOption[]; legacyCount: number };
        if (!Array.isArray(result.items) || !Array.isArray(result.statuses)) throw new Error("请将 Zotero Bridge 更新到 0.2.0 并启用 Reading List");
        this.zoteroStatuses = result.statuses;
        this.zoteroLegacyCount = result.legacyCount || 0;
        this.zoteroLibrary = result.items;
        this.zoteroError = "";
      } catch (error) {
        this.zoteroError = error instanceof Error ? error.message : String(error);
      } finally { this.zoteroLoadedAt = Date.now(); }
    })().finally(() => { this.zoteroRefresh = null; });
    return this.zoteroRefresh;
  }

  async setReadingStatus(article: Article, status: string, collectionDate?: string): Promise<void> {
    const operation = this.zoteroMutations.catch(() => {}).then(async () => {
      await this.zoteroCommand(["--action", "status", "--article-json", JSON.stringify(article),
        "--status", status, "--collection", this.settings.zoteroTargetCollection,
        ...(collectionDate && /^\d{4}-\d{2}-\d{2}$/.test(collectionDate) ? ["--collection-date", collectionDate] : [])], 240000);
      await this.refreshZoteroReading(true);
      this.refreshViews(false);
    });
    this.zoteroMutations = operation;
    return operation;
  }

  async zoteroArticleAction(article: Article, action: "save" | "download" | "open" | "pdf", collectionDate?: string): Promise<void> {
    const operation = this.zoteroMutations.catch(() => {}).then(async () => {
      const key = article.doi || article.id;
      this.articleStates.set(key, {phase: action === "download" ? "正在由 Zotero 下载 PDF…" : "正在操作 Zotero…"});
      this.refreshViews(false);
      try {
        await this.performZoteroArticleAction(article, action, collectionDate);
        await this.inspectZoteroArticle(article);
        if (action === "download" && !this.articleStates.get(key)?.entry?.pdfs?.length) {
          for (let attempt = 0; attempt < 3; attempt++) {
            await new Promise(resolve => window.setTimeout(resolve, 1500));
            await this.inspectZoteroArticle(article);
            if (this.articleStates.get(key)?.entry?.pdfs?.length) break;
          }
          if (!this.articleStates.get(key)?.entry?.pdfs?.length) {
            this.articleStates.set(key, {...this.articleStates.get(key), phase: "尚未发现 PDF；Zotero 可能仍在下载或未找到可用全文，请刷新检查"});
          }
        }
      } catch (error) {
        this.articleStates.set(key, {phase: "操作失败", error: String(error)});
        throw error;
      } finally { this.refreshViews(false); }
    });
    this.zoteroMutations = operation;
    return operation;
  }

  private async performZoteroArticleAction(article: Article, action: "save" | "download" | "open" | "pdf", collectionDate?: string): Promise<void> {
    const command = action === "open" || action === "pdf" ? "lookup" : action;
    const result = await this.zoteroCommand(["--action", command, "--article-json", JSON.stringify(article),
      "--collection", this.settings.zoteroTargetCollection,
      ...(collectionDate && /^\d{4}-\d{2}-\d{2}$/.test(collectionDate) ? ["--collection-date", collectionDate] : [])], 240000) as ZoteroReadingEntry & { message?: string };
    if (action === "open" || action === "pdf") {
      if (!result.key) throw new Error("文献尚未入库，请先保存到 Zotero");
      if (action === "pdf" && !result.pdfs?.length) throw new Error("Zotero 中还没有 PDF 附件，请先下载 PDF");
      const url = action === "pdf" ? result.pdfs[0].url : result.selectUrl;
      if (!/^zotero:\/\/(select|open-pdf)\/library\/items\/[A-Z0-9]{8}$/.test(url)) throw new Error("Zotero 链接无效");
      await require("electron").shell.openExternal(url);
    } else {
      new Notice(action === "save" ? "已保存到 Zotero 收藏夹（已有条目复用并保留原归属）" : result.message || "已交由 Zotero 查找和下载 PDF");
      await this.refreshZoteroReading(true);
      this.refreshViews(false);
    }
  }

  articleStates = new Map<string, {phase: string; entry?: ZoteroReadingEntry; error?: string}>();

  async inspectZoteroArticle(article: Article): Promise<void> {
    const key = article.doi || article.id;
    this.articleStates.set(key, {phase: "正在检查 Zotero…"});
    this.refreshViews(false);
    try {
      const entry = await this.zoteroCommand(["--action", "lookup", "--article-json", JSON.stringify(article)]) as ZoteroReadingEntry;
      this.articleStates.set(key, {phase: entry.key ? (entry.pdfs?.length ? "已入库 · 有 PDF 附件" : "已入库 · 尚无 PDF") : "尚未入库", entry});
    } catch (error) {
      this.articleStates.set(key, {phase: "检查失败", error: String(error)});
    }
    this.refreshViews(false);
  }

  async saveRecommendationFeedback(article: Article, value: string): Promise<void> {
    const result = await this.auxiliaryProbe(["feedback", "--article-json", JSON.stringify(article), "--value", value, "--json"], 20000);
    if (result.code !== 0) throw new Error(result.stderr || "反馈保存失败");
    this.refreshViews(true);
    new Notice(value === "clear" ? "已撤销反馈" : "已调整面板推荐；日报重生成时同步，文献仍完整保留");
  }

  private migrationStatus(status: ReadingStatus): string {
    const target = status === "todo" ? this.settings.readingListTodoStatus : this.settings.readingListReadStatus;
    if (!this.zoteroStatuses.some(option => option.value === target)) throw new Error("请先选择旧待读、已读记录对应的 Reading List 状态");
    return target;
  }

  async migrateReadingLibrary(): Promise<void> {
    await this.settingsWrite;
    await this.refreshZoteroReading(true);
    if (this.zoteroError) throw new Error(this.zoteroError);
    for (const [key, entry] of Object.entries(this.settings.readingLibrary)) {
      const existing = findReadingEntry(this.zoteroLibrary, entry.article);
      if (!existing?.status) await this.setReadingStatus(entry.article, this.migrationStatus(entry.status));
      await this.persistSettings(() => {
        const library = { ...this.settings.readingLibrary };
        delete library[key];
        return { ...this.settings, readingLibrary: library };
      });
    }
    this.refreshViews(false);
  }

  async migrateZoteroTags(): Promise<{ migrated: number; preserved: number; failed: unknown[] }> {
    await this.settingsWrite;
    const result = await this.zoteroCommand(["--action", "migrate", "--todo-status", this.migrationStatus("todo"),
      "--read-status", this.migrationStatus("read")], 240000) as { migrated: number; preserved: number; failed: unknown[] };
    await this.refreshZoteroReading(true);
    this.refreshViews(false);
    return result;
  }

  async updateSettings(patch: Partial<LitradarSettings>): Promise<void> {
    const previous = this.settings;
    await this.persistSettings(() => ({ ...this.settings, ...patch }));

    if (previous.storeDir !== this.settings.storeDir) {
      this.reader = new LitradarStore(this.app, this.settings.storeDir);
      this.refreshViews(true);
    }
    if (
      previous.pythonPath !== this.settings.pythonPath ||
      previous.vaultRoot !== this.settings.vaultRoot
    ) {
      this.rebuildBridge();
      this.scheduleEngineCheck();
    }
    if (previous.minEngine !== this.settings.minEngine) {
      this.reevaluateEngine();
      this.refreshViews(true);
    }
    if (previous.showLogTail !== this.settings.showLogTail) {
      this.refreshViews(false);
    }
    if (previous.autoRunEnabled !== this.settings.autoRunEnabled) {
      this.stopAutoRun();
      if (this.settings.autoRunEnabled) this.startAutoRun();
      this.refreshViews(false);
    }
    if (previous.autoRefreshSeconds !== this.settings.autoRefreshSeconds) {
      this.refreshViews(false);
      this.reapplyPolling();
    }
  }

  get basePath(): string {
    const adapter = this.app.vault.adapter;
    if (adapter instanceof FileSystemAdapter) return adapter.getBasePath();
    const candidate = adapter as unknown as { getBasePath?: () => string };
    return typeof candidate.getBasePath === "function" ? candidate.getBasePath() : "";
  }

  get resolvedVaultRoot(): string {
    return this.settings.vaultRoot.trim() || this.basePath;
  }

  get engineReady(): boolean {
    return this.engine.state === "ok";
  }

  /** Human-readable one-liner for the settings tab. */
  engineSummary(): string {
    switch (this.engine.state) {
      case "ok":
        return `引擎 ${this.engine.engine ?? "?"}（schema ${this.engine.schema ?? "?"}）· ${this.engine.interpreter}`;
      case "below-min":
        return `引擎 ${this.engine.engine ?? "?"}（要求 ≥ ${this.settings.minEngine}）· ${this.engine.interpreter}`;
      case "missing":
        return `找不到 python：${this.engine.interpreter || "（未设置）"}`;
      case "error":
        return `检测失败：${this.engine.detail ?? "未知错误"} · ${this.engine.interpreter}`;
      case "checking":
        return `正在检测引擎 · ${this.engine.interpreter}`;
      default:
        return `尚未检测引擎 · ${this.engine.interpreter || "（未设置）"}`;
    }
  }

  /** Obsidian-side scheduler: runs once a day after schedule.time while Obsidian is open. */
  private startAutoRun(): void {
    if (!this.settings.autoRunEnabled || this.autoTimer !== null) return;
    this.autoStartTimer = window.setTimeout(() => {
      this.autoStartTimer = null;
      void this.maybeAutoRun();
    }, 15_000);
    this.autoTimer = window.setInterval(() => void this.maybeAutoRun(), 60_000);
  }

  private stopAutoRun(): void {
    if (this.autoTimer !== null) window.clearInterval(this.autoTimer);
    if (this.autoStartTimer !== null) window.clearTimeout(this.autoStartTimer);
    this.autoTimer = this.autoStartTimer = null;
  }

  private async maybeAutoRun(): Promise<void> {
    if (!this.settings.autoRunEnabled || this.autoTimer === null || this.job || this.engineBlocker()) return;
    const today = this.today();
    if (this.autoRunDay === today) return;

    const runIds = await this.reader.listRunIds();
    const latestId = runIds.length > 0 ? runIds[runIds.length - 1] : null;
    const store = latestId ? await this.reader.readStore(latestId) : null;
    const fetchedToday = typeof store?.scan_at === "string" && store.scan_at.slice(0, 10) === today;

    if (fetchedToday) {
      const report = latestId
        ? await this.reader.findReport(latestId, store?.window?.end)
        : null;
      if (report) {
        this.autoRunDay = today;
        if (this.settings.autoBackfillEnabled) await this.runAction("maintain");
        return;
      }
      this.autoRunDay = today;
      new Notice("Litradar：今日已抓取但日报缺失，自动生成…", 4000);
      await this.runAction("render");
      return;
    }

    const schedule = await this.reader.readSchedule(today, store);
    const match = /^(\d{1,2}):(\d{2})/.exec(schedule.time.trim());
    if (!match) return;
    const target = Number(match[1]) * 60 + Number(match[2]);
    const now = new Date();
    if (now.getHours() * 60 + now.getMinutes() < target) return;

    this.autoRunDay = today;
    new Notice(`Litradar：已到计划时间 ${schedule.time}，开始自动抓取…`, 4000);
    if (this.settings.autoBackfillEnabled) await this.runAction("maintain");
    if (this.settings.autoRunEnabled && !this.job && this.lastOutcome?.kind !== "cancelled") await this.runAction("run");
  }

  /** Non-null exactly when engine actions must be disabled. */
  engineBlocker(): string | null {
    if (this.engine.state === "ok") return null;
    if (this.engine.state === "below-min") {
      return `引擎 ${this.engine.engine ?? "?"} 低于插件要求 ${this.settings.minEngine}，请执行 \`pipx upgrade litradar\``;
    }
    if (this.engine.state === "missing") {
      return this.engine.interpreter
        ? `找不到 python：${this.engine.interpreter}`
        : "未设置 pythonPath";
    }
    if (this.engine.state === "checking" || this.engine.state === "unknown") {
      return "正在检测引擎…";
    }
    return `无法检测引擎：${this.engine.detail ?? "未知错误"}`;
  }

  /** Effective engine config (api_key redacted by the engine) for the settings UI. */
  async engineConfig(): Promise<Record<string, unknown> | null> {
    if (!this.bridge) return null;
    try {
      const result = await this.auxiliaryProbe(["config", "list", "--json"], 8000);
      return (result.json ?? null) as Record<string, unknown> | null;
    } catch {
      return null;
    }
  }

  /** Zotero collections for the profile builder, with a readable failure reason. */
  async engineCollections(): Promise<{
    collections: ZoteroCollection[] | null;
    error: string;
  }> {
    if (!this.bridge) return { collections: null, error: "引擎桥未初始化" };
    try {
      const result = await this.auxiliaryProbe(["zotero", "collections", "--json"], 20000);
      if (Array.isArray(result.json)) {
        return { collections: result.json as ZoteroCollection[], error: "" };
      }
      return { collections: null, error: tailLines(result.stderr || result.stdout, 3) || `退出码 ${result.code ?? "null"}` };
    } catch (error) {
      return { collections: null, error: String(error) };
    }
  }

  /** Build a profile from the checked collections (LLM, falls back to keywords). */
  async engineProfile(collections: string[], name: string): Promise<{ ok: boolean; error: string }> {
    if (!this.bridge) return { ok: false, error: "引擎桥未初始化" };
    const args = ["zotero", "profile"];
    if (collections.length > 0) args.push("--collections", collections.join(","));
    args.push("--llm", "--write");
    if (name.trim()) args.push("--name", name.trim());
    try {
      const result = await this.bridge.probeJson(args, 180000);
      return {
        ok: result.code === 0,
        error: result.code === 0 ? "" : tailLines(result.stderr || result.stdout, 3),
      };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }

  /** Persist one setting through the engine (writes .litradar/config.local.yaml). */
  async engineConfigSet(key: string, value: string): Promise<boolean> {
    if (!this.bridge) return false;
    try {
      const result = await this.bridge.probeJson(["config", "set", key, value], 8000);
      if (result.code !== 0) return false;
      if (key.startsWith("schedule.")) this.refreshViews(true);
      return true;
    } catch {
      return false;
    }
  }

  private makeBridge(): void {
    this.checkToken += 1;
    this.bridge = new Bridge({
      pythonPath: this.settings.pythonPath,
      vaultRoot: this.resolvedVaultRoot,
    });
  }

  private rebuildBridge(): void {
    if (this.job) {
      // Never swap the bridge while a child owns it; the pending rebuild happens
      // when the job finishes so cancel still reaches the process.
      this.bridgeDirty = true;
      return;
    }
    this.makeBridge();
  }

  private scheduleEngineCheck(): void {
    if (this.checkTimer !== null) window.clearTimeout(this.checkTimer);
    this.checkTimer = window.setTimeout(() => {
      this.checkTimer = null;
      void this.checkEngine(false);
    }, 800);
  }

  /** Re-evaluate the stored engine version against a changed minEngine. */
  reevaluateEngine(): void {
    if (this.engine.state === "ok" || this.engine.state === "below-min") {
      if (!this.engine.engine) return;
      this.engine = {
        ...this.engine,
        state: compareVersions(this.engine.engine, this.settings.minEngine) < 0 ? "below-min" : "ok",
      };
      this.refreshViews(false);
    }
  }

  async checkEngine(manual = false): Promise<EngineStatus> {
    if (this.engineChecking || this.job) return this.engine;
    if (!this.settings.pythonPath.trim()) {
      this.engine = {
        state: "missing",
        interpreter: "",
        detail: "pythonPath 为空",
        checkedAt: Date.now(),
      };
      this.refreshViews(false);
      if (manual) new Notice("请先设置 Python 解释器路径");
      return this.engine;
    }

    const token = ++this.checkToken;
    this.engineChecking = true;
    this.engine = {
      ...this.engine,
      state: "checking",
      interpreter: this.settings.pythonPath,
      detail: undefined,
    };
    this.refreshViews(false);

    let next: EngineStatus;
    try {
      const result = await this.bridge.probeJson(["--version", "--json"], 5000);
      const payload = (result.json ?? null) as VersionPayload | null;
      if (result.code !== 0 || !payload || typeof payload.engine !== "string") {
        const stderr = tailLines(result.stderr, 5);
        next = {
          state: "error",
          interpreter: this.settings.pythonPath,
          schema: payload?.schema,
          detail:
            result.code === EXIT.CONFIG
              ? "引擎拒绝了命令（退出码 2）"
              : stderr || `退出码 ${result.code ?? "null"}`,
          checkedAt: Date.now(),
        };
      } else {
        const state = compareVersions(payload.engine, this.settings.minEngine) < 0
          ? "below-min"
          : "ok";
        next = {
          state,
          engine: payload.engine,
          schema: typeof payload.schema === "number" ? payload.schema : undefined,
          interpreter: this.settings.pythonPath,
          checkedAt: Date.now(),
        };
      }
    } catch (error) {
      if (error instanceof BridgeError && error.kind === "enoent") {
        next = {
          state: "missing",
          interpreter: this.settings.pythonPath,
          detail: error.message,
          checkedAt: Date.now(),
        };
      } else {
        next = {
          state: "error",
          interpreter: this.settings.pythonPath,
          detail: error instanceof Error ? error.message : String(error),
          checkedAt: Date.now(),
        };
      }
    } finally {
      this.engineChecking = false;
    }

    // A newer check (or a settings change) superseded this one.
    if (token !== this.checkToken) return this.engine;
    this.engine = next;
    this.refreshViews(false);

    if (manual) {
      if (next.state === "ok") {
        new Notice(
          `引擎 ${next.engine}（schema ${next.schema ?? "?"}）· ${next.interpreter}`,
          6000,
        );
      } else {
        new Notice(this.engineBlocker() ?? "引擎检测失败", 8000);
      }
    }
    return next;
  }

  // --------------------------------------------------------------------- view

  async activateView(): Promise<void> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE_LITRADAR);
    let leaf: WorkspaceLeaf | null = existing.length > 0 ? existing[0] : null;
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      if (!leaf) return;
      await leaf.setViewState({ type: VIEW_TYPE_LITRADAR, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
  }

  subscribeJob(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emitJob(): void {
    for (const listener of Array.from(this.listeners)) {
      try {
        listener();
      } catch {
        // A closed view must not break the other listeners.
      }
    }
  }

  refreshViews(refreshData: boolean): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_LITRADAR)) {
      const view = leaf.view;
      if (view instanceof LitradarView) {
        view.applySettings();
        if (refreshData) void view.refresh();
        else view.render();
      }
    }
  }

  private reapplyPolling(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_LITRADAR)) {
      const view = leaf.view;
      if (view instanceof LitradarView) view.applySettings();
    }
  }

  // ----------------------------------------------------------------- commands

  private async newestStore(): Promise<{ runId: string | null; store: StorePayload | null }> {
    const runIds = await this.reader.listRunIds();
    if (runIds.length === 0) return { runId: null, store: null };
    const runId = runIds[runIds.length - 1];
    const store = await this.reader.readStore(runId);
    return { runId, store };
  }

  async runAction(action: ActionKind, options?: {runId?: string; journal?: string}): Promise<void> {
    if (this.job) {
      new Notice("任务进行中，请稍候");
      return;
    }
    const blocker = this.engineBlocker();
    if (blocker) {
      new Notice(blocker, 8000);
      return;
    }

    const runIds = await this.reader.listRunIds();
    let runId = runIds.length > 0 ? runIds[runIds.length - 1] : null;
    let store: StorePayload | null = null;
    if (options?.runId) runId = options.runId;
    if (runId) store = await this.reader.readStore(runId);

    let args: string[];
    if (action === "run") {
      // Full pipeline: fetch + LLM render. The engine owns the date window.
      args = ["run", "--llm"];
    } else if (action === "repair") {
      if (!runId) { new Notice("没有可重试的批次"); return; }
      args = ["repair", "--run-id", runId, "--no-llm"];
      if (options?.journal) args.push("--journal", options.journal);
    } else if (action === "maintain") {
      args = ["maintain", "--days", String(Math.max(1,Math.min(7,this.settings.backfillDays || 3))), "--no-llm"];
    } else if (action === "schedule") {
      args = ["schedule", "install", "--apply"];
    } else if (action === "fetch") {
      // The engine owns the date window (config `schedule.window`); the plugin
      // must not compute one.
      args = ["fetch"];
    } else if (action === "render" || action === "embed") {
      if (!runId || !store) {
        new Notice("今日尚未抓取：先运行「抓取并生成」", 6000);
        return;
      }
      args = [action];
      if (action === "render") args.push("--llm");
      if (store.window?.start && store.window.end && store.window.start === store.window.end) {
        // Single-day window: the engine-provided end date is exactly the store
        // date. This is the documented `render --date <date>` path.
        args.push("--date", store.window.end);
      } else {
        // Ranges/weekly runs cannot be expressed with `--date`; address the
        // exact store the panel is showing.
        args.push("--run-id", runId);
      }
    } else {
      args = ["doctor", "--json"];
    }

    const job: JobState = {
      action,
      startedAt: Date.now(),
      runId: runId ?? undefined,
      phase:
        action === "fetch" || action === "run"
          ? "启动引擎…"
          : action === "schedule"
            ? "写入 LaunchAgent…"
            : "读取 store…",
      scans: {},
      cancelRequested: false,
      stderrTail: "",
    };
    this.job = job;
    this.lastOutcome = null;
    if (action === "render" || action === "embed") this.doctor = null;
    this.emitJob();

    try {
      const result = await this.bridge.run(args, TIMEOUTS[action], (event) =>
        this.handleEvent(event),
      );
      this.finishJob(result.code, result.stderr, {
        timedOut: result.timedOut,
        cancelled: result.cancelled,
      });
    } catch (error) {
      this.failJob(error);
    }
  }

  private handleEvent(event: RunEvent): void {
    const job = this.job;
    if (!job) return;
    const text = (value: unknown): string | undefined =>
      typeof value === "string" ? value : undefined;
    const number = (value: unknown): number | undefined =>
      typeof value === "number" && Number.isFinite(value) ? value : undefined;

    switch (event.event) {
      case "run_start": {
        job.runId = text(event.run_id) ?? job.runId;
        job.phase = "开始运行";
        break;
      }
      case "journal_start": {
        const journal = text(event.journal);
        if (journal) {
          job.scans[journal] = { status: "running" };
          job.phase = `抓取 ${journal}`;
        }
        break;
      }
      case "journal_done": {
        const journal = text(event.journal);
        const status = text(event.status);
        if (journal && status) {
          job.scans[journal] = {
            status: status as JobState["scans"][string]["status"],
            http: number(event.http) ?? null,
            items: number(event.items),
            reason: text(event.reason) ?? null,
          };
          job.phase = `${journal}：${status}`;
        }
        break;
      }
      case "progress": {
        const stage = text(event.stage) ?? "运行中";
        const done = number(event.done) ?? 0;
        const total = number(event.total) ?? 0;
        job.progress = { stage, done, total };
        job.phase = `${stage} ${done}/${total}`;
        break;
      }
      case "report_written": {
        job.reportPath = text(event.path);
        job.items = number(event.items);
        job.highlights = number(event.highlights);
        job.coverage = text(event.coverage);
        job.phase = "日报已生成";
        break;
      }
      case "run_end": {
        const exit = number(event.exit);
        if (exit !== undefined) job.phase = `结束（退出码 ${exit}）`;
        break;
      }
      default:
        // Unknown events are deliberately ignored.
        break;
    }
    this.emitJob();
  }

  private finishJob(
    code: number | null,
    stderr: string,
    flags: { timedOut: boolean; cancelled: boolean },
  ): void {
    const job = this.job;
    if (!job) return;
    const stderrTail = tailLines(stderr, 20);
    job.stderrTail = stderrTail;

    const cancelled = flags.cancelled || job.cancelRequested || code === EXIT.CANCELLED;
    let kind: OutcomeKind;
    let message: string;
    let detail: string | undefined;
    let notice: { text: string; ms: number } | null = null;

    if (flags.timedOut) {
      kind = "cancelled";
      message = "已取消（超时）";
      detail = `超过 ${Math.round(TIMEOUTS[job.action] / 1000)} 秒，已终止进程树`;
      notice = { text: message, ms: 5000 };
    } else if (cancelled && code !== EXIT.OK) {
      kind = "cancelled";
      message = "已取消";
      notice = { text: message, ms: 4000 };
    } else {
      switch (code) {
        case EXIT.OK:
          kind = "ok";
          message = "已完成";
          break;
        case EXIT.CONFIG:
          kind = "config";
          message = "配置有误";
          detail = stderrTail;
          notice = { text: "配置有误，请检查 .litradar/config.yaml", ms: 8000 };
          break;
        case EXIT.SCREEN:
          kind = "screen";
          message = "模型输出被降级/拒收";
          detail = "详见日报的「校验记录」";
          break;
        case EXIT.ALL_FAILED:
          // The semantic iron rule: never render "无新文章" for this outcome.
          kind = "all-failed";
          message = "今日抓取全部失败";
          detail = stderrTail;
          notice = { text: "今日抓取全部失败", ms: 10000 };
          break;
        case EXIT.DOCTOR:
          kind = "doctor";
          message = "引擎自检发现问题";
          break;
        case EXIT.LOCKED:
          kind = "locked";
          message = "任务进行中";
          break;
        case EXIT.CANCELLED:
          kind = "cancelled";
          message = "已取消";
          notice = { text: message, ms: 4000 };
          break;
        case null:
          kind = "error";
          message = "引擎异常退出";
          detail = stderrTail;
          notice = { text: message, ms: 6000 };
          break;
        default:
          if (code === EXIT.FAIL) {
            kind = "error";
            message = "运行失败（退出码 1）";
          } else {
            kind = "error";
            message = `运行失败（退出码 ${code}）`;
          }
          detail = stderrTail;
          notice = { text: message, ms: 8000 };
          break;
      }
    }

    this.lastOutcome = {
      kind,
      action: job.action,
      code,
      message,
      detail,
      stderrTail,
      reportPath: job.reportPath,
      finishedAt: Date.now(),
    };
    this.job = null;
    if (this.bridgeDirty) {
      this.bridgeDirty = false;
      this.makeBridge();
    }
    this.emitJob();
    this.refreshViews(true);
    if (notice) new Notice(notice.text, notice.ms);
  }

  private failJob(error: unknown): void {
    const job = this.job;
    if (!job) return;
    let kind: OutcomeKind = "error";
    let message = "无法启动引擎";
    let detail: string | undefined;
    if (error instanceof BridgeError) {
      if (error.kind === "enoent") {
        kind = "enoent";
        message = `找不到 python：${error.path ?? this.settings.pythonPath}`;
        detail = "请在设置中填写 Python 解释器的绝对路径（不依赖 PATH）。";
      } else if (error.kind === "busy") {
        kind = "locked";
        message = "任务进行中";
      } else {
        detail = error.message;
      }
    } else if (error instanceof Error) {
      detail = error.message;
    } else {
      detail = String(error);
    }

    this.lastOutcome = {
      kind,
      action: job.action,
      code: null,
      message,
      detail,
      stderrTail: job.stderrTail,
      reportPath: job.reportPath,
      finishedAt: Date.now(),
    };
    this.job = null;
    if (this.bridgeDirty) {
      this.bridgeDirty = false;
      this.makeBridge();
    }
    this.emitJob();
    this.refreshViews(true);
    new Notice(kind === "enoent" ? `${message}（打开设置填写路径）` : message, 8000);
  }

  async runDoctor(): Promise<void> {
    if (this.job) {
      new Notice("任务进行中，请稍候");
      return;
    }
    const blocker = this.engineBlocker();
    if (blocker) {
      new Notice(blocker, 8000);
      return;
    }

    const job: JobState = {
      action: "doctor",
      startedAt: Date.now(),
      phase: "自检中…",
      scans: {},
      cancelRequested: false,
      stderrTail: "",
    };
    this.job = job;
    this.lastOutcome = null;
    this.emitJob();

    try {
      const result = await this.bridge.probeJson(["doctor", "--json"], TIMEOUTS.doctor);
      const payload = (result.json ?? null) as DoctorPayload | null;
      const stderrTail = tailLines(result.stderr, 20);
      const code = result.code;
      const kind: OutcomeKind =
        code === EXIT.OK ? "ok" : code === EXIT.DOCTOR ? "doctor" : code === EXIT.CONFIG ? "config" : "error";
      this.doctor = payload;
      this.lastOutcome = {
        kind,
        action: "doctor",
        code,
        message:
          code === EXIT.OK
            ? "引擎自检通过"
            : payload?.summary ?? (code === EXIT.DOCTOR ? "引擎自检发现问题" : "自检运行失败"),
        detail: stderrTail,
        stderrTail,
        reportPath: job.reportPath,
        doctor: payload ?? undefined,
        finishedAt: Date.now(),
      };
      this.job = null;
      if (this.bridgeDirty) {
        this.bridgeDirty = false;
        this.makeBridge();
      }
      this.emitJob();
      this.refreshViews(true);

      const summary = payload?.summary ?? (code === EXIT.OK ? "通过" : `退出码 ${code}`);
      if (code === EXIT.OK) new Notice(`引擎自检通过：${summary}`, 5000);
      else new Notice(`引擎自检：${summary}`, 8000);
    } catch (error) {
      this.failJob(error);
    }
  }

  cancelJob(): void {
    if (!this.job) return;
    this.job.cancelRequested = true;
    this.job.phase = "正在取消…";
    this.bridge.cancel();
    this.emitJob();
  }

  async openReport(runIdArg?: string, preferredPath?: string): Promise<void> {
    const runIds = await this.reader.listRunIds();
    const runId = runIdArg ?? (runIds.length > 0 ? runIds[runIds.length - 1] : null);
    if (!runId) {
      new Notice("尚未抓取，暂无日报");
      return;
    }
    const store = await this.reader.readStore(runId);
    const preferred =
      preferredPath ??
      (!runIdArg && this.lastOutcome?.action !== "doctor" ? this.lastOutcome?.reportPath : undefined);
    const path = await this.reader.findReport(runId, store?.window?.end, preferred);
    if (!path) {
      new Notice(`尚未生成日报：${runId}（先运行「重生成」）`, 6000);
      return;
    }
    const file = this.reader.getIndexedFile(path);
    if (file) {
      await this.app.workspace.getLeaf(false).openFile(file);
      return;
    }
    try {
      // `.litradar/runs/<id>/report.md` may be outside Obsidian's index.
      await this.app.workspace.openLinkText(path, "", false);
    } catch {
      new Notice(`无法打开日报：${path}`, 6000);
    }
  }

  today(): string {
    const now = new Date();
    const pad = (value: number): string => String(value).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }

  actionLabel(action: ActionKind): string {
    return ACTION_LABELS[action];
  }
}

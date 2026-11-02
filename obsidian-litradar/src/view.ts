/**
 * The Litradar panel.
 *
 * Rendering is intentionally dumb: every value shown here was written by the
 * engine (store/<run>.json, runs/<run>/*.json, report.md front-matter). The
 * panel never derives relevance, date windows or Markdown, and it never writes
 * to `.litradar`.
 */

import { ItemView, WorkspaceLeaf } from "obsidian";
import type LitradarPlugin from "./main";
import { dateSourceNote } from "./store";
import {
  SCHEMA_VERSION,
  type ActionKind,
  type Article,
  type LiveScan,
  type PanelData,
  type ScanStatus,
  type ScreenResult,
  type StorePayload,
} from "./types";

export const VIEW_TYPE_LITRADAR = "litradar-view";

const SCAN_LABELS: Record<string, string> = {
  ok: "✅ 已读",
  "metadata-only": "⚠ 仅元数据",
  blocked: "⛔ 被拦截",
  timeout: "⏱ 超时",
  error: "❌ 解析失败",
  running: "⏳ 进行中",
};

const FAILED_STATUSES: ScanStatus[] = ["blocked", "timeout", "error"];

function isHealthy(status: string | undefined): boolean {
  return status === "ok" || status === "metadata-only";
}

function isFailed(status: string | undefined): boolean {
  return status === "blocked" || status === "timeout" || status === "error";
}

function scanLabel(status: string | undefined): string {
  return SCAN_LABELS[status ?? ""] ?? "?";
}

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function formatTime(iso: string | null | undefined): string {
  const parsed = parseDate(iso);
  if (!parsed) return iso ?? "—";
  return `${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}`;
}

function formatDateTime(iso: string | null | undefined): string {
  const parsed = parseDate(iso);
  if (!parsed) return iso ?? "—";
  return (
    `${parsed.getFullYear()}-${pad2(parsed.getMonth() + 1)}-${pad2(parsed.getDate())} ` +
    `${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}`
  );
}

function articleDate(item: Article): string {
  if (!item.online_date) return "官网未标注";
  return `${item.online_date}${dateSourceNote(item.date_source)}`;
}

function safeExternalUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return /^https?:\/\//i.test(url) ? url : null;
}

function asArticles(value: unknown): Article[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is Article =>
      !!entry &&
      typeof entry === "object" &&
      typeof (entry as Article).id === "string" &&
      typeof (entry as Article).title === "string",
  );
}

function newArticles(store: StorePayload | null): Article[] {
  return asArticles(store?.items).filter((item) => !item.already_seen);
}

function screenResults(data: PanelData): ScreenResult[] {
  const results = data.screen?.results;
  if (!Array.isArray(results)) return [];
  return results.filter(
    (entry): entry is ScreenResult =>
      !!entry && typeof entry === "object" && typeof (entry as ScreenResult).id === "string",
  );
}

function storeScans(store: StorePayload | null) {
  if (!store || !Array.isArray(store.journals)) return [];
  return store.journals.filter(
    (scan): scan is NonNullable<typeof scan> =>
      !!scan && typeof scan === "object" && typeof scan.journal === "string",
  );
}

function allFailedOf(data: PanelData): boolean {
  const scans = storeScans(data.store);
  return scans.length > 0 && scans.every((scan) => !isHealthy(scan.status));
}

function noNewItemsOf(data: PanelData): boolean {
  const scans = storeScans(data.store);
  return (
    scans.length > 0 &&
    scans.every((scan) => scan.status === "ok" && (scan.items_seen ?? 0) === 0)
  );
}

interface HighlightCard {
  result: ScreenResult;
  item: Article;
}

export class LitradarView extends ItemView {
  private snapshot: PanelData | null = null;
  private loading = false;
  private pendingRefresh = false;
  private timer: number | null = null;
  private unsubscribe: (() => void) | null = null;
  private wasRunning = false;
  private closed = false;

  constructor(
    leaf: WorkspaceLeaf,
    private readonly plugin: LitradarPlugin,
  ) {
    super(leaf);
  }

  getViewType(): string {
    return VIEW_TYPE_LITRADAR;
  }

  getDisplayText(): string {
    return "文献雷达";
  }

  getIcon(): string {
    return "radar";
  }

  async onOpen(): Promise<void> {
    this.contentEl.addClass("litradar-view");
    this.unsubscribe = this.plugin.subscribeJob(() => this.onJobChange());
    this.registerDomEvent(window, "focus", () => {
      if (!this.plugin.job) void this.refresh();
    });
    this.applySettings();
    await this.refresh();
  }

  async onClose(): Promise<void> {
    this.closed = true;
    this.stopPolling();
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.contentEl.empty();
  }

  /** Called by the plugin whenever settings or the poll interval change. */
  applySettings(): void {
    if (this.closed) return;
    this.startPolling();
    this.render();
  }

  private startPolling(): void {
    this.stopPolling();
    const seconds = Number(this.plugin.settings.autoRefreshSeconds);
    if (!Number.isFinite(seconds) || seconds <= 0) return;
    this.timer = window.setInterval(() => {
      if (this.closed || this.plugin.job || document.hidden) return;
      void this.refresh();
    }, Math.max(2, seconds) * 1000);
  }

  private stopPolling(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  private onJobChange(): void {
    if (this.closed) return;
    const running = !!this.plugin.job;
    if (this.wasRunning && !running) {
      // Completion invalidates the snapshot: re-read the engine's files.
      void this.refresh();
    } else {
      this.render();
    }
    this.wasRunning = running;
  }

  /**
   * Re-read `.litradar`. Missing-today data is never cached: every poll lists
   * `store/` again, so a manual `litradar fetch` shows up on the next tick.
   */
  async refresh(): Promise<void> {
    if (this.closed) return;
    if (this.loading) {
      this.pendingRefresh = true;
      return;
    }
    this.loading = true;
    try {
      const data = await this.plugin.reader.loadPanelData(this.plugin.today());
      if (this.closed) return;
      this.snapshot = data;
      this.render();
    } catch (error) {
      if (this.closed) return;
      this.snapshot = {
        initialized: false,
        storeDir: this.plugin.reader.storeDir,
        runIds: [],
        activeRunId: null,
        store: null,
        scans: {},
        screen: null,
        scores: null,
        report: null,
        reportText: null,
        runLogTail: null,
        today: this.plugin.today(),
        fetchedToday: false,
        warnings: [],
        error: error instanceof Error ? error.message : String(error),
      };
      this.render();
    } finally {
      this.loading = false;
      if (this.pendingRefresh && !this.closed) {
        this.pendingRefresh = false;
        void this.refresh();
      }
    }
  }

  // ------------------------------------------------------------------ render

  render(): void {
    if (this.closed) return;
    const container = this.contentEl;
    container.empty();
    container.addClass("litradar-view");

    const data = this.snapshot;
    this.renderHeader(container, data);
    this.renderBanners(container, data);
    this.renderToolbar(container, data);

    if (!data) {
      container.createDiv({ cls: "litradar-loading", text: "正在读取 .litradar…" });
      return;
    }
    if (data.error) {
      const section = container.createEl("section", { cls: "litradar-panel litradar-panel-error" });
      section.createEl("h5", { text: "读取失败" });
      section.createEl("p", { text: data.error });
      this.renderLog(container, data);
      this.renderFooter(container, data);
      return;
    }
    if (!data.initialized) {
      this.renderUninitialized(container, data);
      this.renderFooter(container, data);
      return;
    }
    if (!data.activeRunId || !data.store) {
      this.renderNoStore(container, data);
      this.renderLog(container, data);
      this.renderFooter(container, data);
      return;
    }

    this.renderCoverage(container, data);
    this.renderHighlights(container, data);
    this.renderAllItems(container, data);
    this.renderFailures(container, data);
    this.renderValidation(container, data);
    this.renderDoctor(container);
    this.renderLog(container, data);
    this.renderFooter(container, data);
  }

  private renderHeader(container: HTMLElement, data: PanelData | null): void {
    const header = container.createDiv({ cls: "litradar-header" });
    const job = this.plugin.job;
    const store = data?.store ?? null;
    const label =
      store?.window?.label ??
      store?.run_id ??
      data?.activeRunId ??
      job?.runId ??
      data?.today ??
      this.plugin.today();
    header.createEl("h4", { text: `文献雷达 · ${label}` });

    const status = header.createDiv({ cls: "litradar-status" });
    if (job) {
      status.addClass("is-running");
      const progress = job.progress;
      const suffix =
        progress && progress.total > 0 ? ` · ${progress.done}/${progress.total}` : "";
      status.setText(`⏳ 运行中 · ${this.plugin.actionLabel(job.action)} · ${job.phase}${suffix}`);
      const bar = header.createDiv({ cls: "litradar-progress" });
      const fill = bar.createDiv({ cls: "litradar-progress-fill" });
      if (progress && progress.total > 0) {
        fill.style.width = `${Math.max(0, Math.min(100, (progress.done / progress.total) * 100))}%`;
      } else {
        fill.addClass("litradar-progress-indeterminate");
      }
      return;
    }

    if (!data) {
      status.addClass("is-muted");
      status.setText("正在读取…");
      return;
    }
    if (data.error) {
      status.addClass("is-fail");
      status.setText("❌ 读取失败");
      return;
    }
    if (!data.initialized) {
      status.addClass("is-muted");
      status.setText("尚未初始化");
      return;
    }
    if (!data.store) {
      status.addClass("is-muted");
      status.setText("今日尚未抓取");
      return;
    }

    const scans = storeScans(data.store);
    const allFailed = allFailedOf(data);
    const items = data.report?.new_items ?? newArticles(data.store).length;
    const highlights =
      data.report?.highlights ??
      (screenResults(data).filter((result) => result.relevant === true).length);
    const coverage = data.report?.coverage ?? "—";
    const icon = allFailed ? "❌" : scans.some((scan) => isFailed(scan.status)) ? "⚠️" : "✅";
    status.addClass(allFailed ? "is-fail" : "is-ok");
    status.setText(
      `${icon} 已更新 ${formatTime(data.store.scan_at)} · 覆盖 ${coverage} · ${items} 篇 / 置顶 ${highlights}`,
    );
    if (!data.fetchedToday) {
      status.addClass("is-stale");
    }
  }

  private renderBanners(container: HTMLElement, data: PanelData | null): void {
    const blocker = this.plugin.engineBlocker();
    if (blocker) {
      const engine = this.plugin.engine;
      const checking = engine.state === "checking" || engine.state === "unknown";
      this.banner(
        container,
        checking ? "info" : "error",
        engine.state === "below-min" ? "引擎版本不满足要求" : "引擎不可用",
        blocker,
        [
          { label: "打开设置", onClick: () => this.openSettings() },
          { label: "重新检测", onClick: () => void this.plugin.checkEngine(true) },
        ],
      );
    }

    const outcome = this.plugin.lastOutcome;
    if (outcome && outcome.kind !== "ok" && outcome.kind !== "locked") {
      const actions: BannerAction[] = [];
      if (outcome.kind === "enoent" || outcome.kind === "config") {
        actions.push({ label: "打开设置", onClick: () => this.openSettings() });
      }
      if (outcome.kind === "screen" || outcome.kind === "all-failed") {
        actions.push({
          label: "打开日报",
          onClick: () => void this.plugin.openReport(data?.activeRunId ?? undefined),
        });
      }
      this.banner(
        container,
        outcome.kind === "screen" || outcome.kind === "doctor" ? "warn" : outcome.kind === "cancelled" ? "muted" : "error",
        outcome.message,
        outcome.detail,
        actions,
        outcome.stderrTail,
      );
    }

    if (data?.store && !data.fetchedToday) {
      this.banner(
        container,
        "warn",
        "今日尚未抓取",
        `当前显示最近一次 ${data.store.window?.label ?? data.activeRunId ?? ""}` +
          `（扫描于 ${formatDateTime(data.store.scan_at)}）。下一次轮询会重新检查。`,
        [{ label: "重抓", onClick: () => void this.plugin.runAction("fetch") }],
      );
    }

    if (data?.store && typeof data.store.schema === "number" && data.store.schema > SCHEMA_VERSION) {
      this.banner(
        container,
        "warn",
        "数据结构版本高于插件",
        `store schema=${data.store.schema}（插件支持 ${SCHEMA_VERSION}）。请升级插件，不要修改 store。`,
      );
    }
  }

  private renderToolbar(container: HTMLElement, data: PanelData | null): void {
    const toolbar = container.createDiv({ cls: "litradar-toolbar" });
    const job = this.plugin.job;
    const engineBlocked = this.plugin.engineBlocker();
    const busy = !!job;
    const initialized = !!data?.initialized;
    const hasStore = !!data?.store;
    const hasReport = !!data?.report;

    const reason = (extra?: string): string | undefined =>
      engineBlocked ?? (busy ? "任务进行中" : extra);

    const add = (
      label: string,
      action: ActionKind,
      enabled: boolean,
      reasonText?: string,
    ): void => {
      const button = toolbar.createEl("button", { cls: "litradar-btn", text: label });
      button.disabled = !enabled;
      if (!enabled && reasonText) button.title = reasonText;
      button.onclick = () => {
        void this.plugin.runAction(action);
      };
    };

    add("重抓", "fetch", !engineBlocked && !busy && initialized, reason("尚未初始化"));
    add("重生成", "render", !engineBlocked && !busy && hasStore, reason("今日尚未抓取"));
    add("算相关度", "embed", !engineBlocked && !busy && hasStore, reason("今日尚未抓取"));

    const reportButton = toolbar.createEl("button", { cls: "litradar-btn", text: "打开日报" });
    reportButton.disabled = !!engineBlocked || busy || !hasReport;
    reportButton.title = engineBlocked ?? (busy ? "任务进行中" : hasReport ? "" : "尚未生成日报");
    reportButton.onclick = () => {
      void this.plugin.openReport(data?.activeRunId ?? undefined);
    };

    if (busy) {
      const cancel = toolbar.createEl("button", {
        cls: "litradar-btn litradar-btn-danger",
        text: "取消",
      });
      cancel.title = "终止引擎进程树";
      cancel.onclick = () => this.plugin.cancelJob();
    }
  }

  private renderCoverage(container: HTMLElement, data: PanelData): void {
    const section = container.createEl("section", { cls: "litradar-panel litradar-coverage" });
    section.createEl("h5", { text: "覆盖情况" });

    const storeRows = storeScans(data.store);
    const job = this.plugin.job;
    const live = job?.scans ?? {};
    const names: string[] = storeRows.map((scan) => scan.journal);
    for (const name of Object.keys(live)) {
      if (!names.includes(name)) names.push(name);
    }

    if (names.length === 0) {
      section.createDiv({ cls: "litradar-empty-line", text: "本日没有期刊扫描记录。" });
      return;
    }

    const table = section.createEl("table", { cls: "litradar-table" });
    const head = table.createEl("thead").createEl("tr");
    for (const title of ["期刊", "栏目", "状态", "条数", "上次成功"]) {
      head.createEl("th", { text: title });
    }
    const body = table.createEl("tbody");
    let failedCount = 0;

    for (const name of names) {
      const stored = storeRows.find((scan) => scan.journal === name);
      const liveScan: LiveScan | undefined = live[name];
      const status = liveScan?.status ?? stored?.status ?? "error";
      const items = liveScan?.items ?? stored?.items_seen ?? 0;
      const reason = liveScan?.reason ?? stored?.reason ?? null;
      const history = data.scans[name];
      const row = body.createEl("tr");
      if (isFailed(status)) {
        failedCount += 1;
        row.addClass("litradar-row-fail");
      }

      const journalCell = row.createEl("td");
      journalCell.createEl("div", { text: name });
      if (reason) journalCell.createEl("div", { cls: "litradar-reason", text: String(reason) });
      if ((history?.consecutive_failures ?? 0) >= 3) {
        journalCell.createEl("div", {
          cls: "litradar-reason litradar-reason-bad",
          text: `连续失败 ${history?.consecutive_failures} 次`,
        });
      }

      row.createEl("td", { text: stored?.surface ?? "—" });
      const statusCell = row.createEl("td", {
        text: scanLabel(status),
        cls: `litradar-scan litradar-scan-${status}`,
      });
      if (liveScan) statusCell.addClass("is-live");
      row.createEl("td", { text: String(items) });
      row.createEl("td", { text: formatDateTime(history?.last_ok_at ?? null) });
    }

    const scans = storeRows;
    if (scans.length > 0 && scans.every((scan) => !isHealthy(scan.status))) {
      section.addClass("litradar-panel-fail");
      section.createDiv({
        cls: "litradar-empty-line litradar-error-text",
        text: "全部期刊抓取失败：本日覆盖不可用，无法判断是否有新条目。",
      });
    } else if (failedCount > 0) {
      section.createDiv({
        cls: "litradar-empty-line litradar-warn-text",
        text: `${failedCount} 本刊抓取异常；覆盖不完整，结论只对成功的刊成立。`,
      });
    }
  }

  private renderHighlights(container: HTMLElement, data: PanelData): void {
    const section = container.createEl("section", { cls: "litradar-panel litradar-highlights" });
    const cards = this.highlightCards(data);
    const cap = data.report?.highlights ?? 8;
    section.createEl("h5", {
      text: `与你最相关（${Math.min(cards.length, Math.max(0, cap))} 篇）`,
    });

    if (cards.length === 0) {
      let message: string;
      if (!data.screen) {
        message = "未找到 screen.json：本轮可能未启用 LLM，或尚未生成筛选结果。";
      } else if (screenResults(data).length === 0) {
        message = "本轮没有置顶条目。";
      } else {
        message = "screen.json 中没有可展示的置顶条目（可能全部为不相关）。";
      }
      section.createDiv({ cls: "litradar-empty-line", text: message });
      return;
    }

    for (const card of cards.slice(0, Math.max(0, cap))) {
      const item = card.item;
      const result = card.result;
      const cardEl = section.createDiv({ cls: "litradar-card" });
      const titleRow = cardEl.createDiv({ cls: "litradar-card-title" });
      const url = safeExternalUrl(item.url);
      if (url) {
        const link = titleRow.createEl("a", { text: item.title, href: url });
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      } else {
        titleRow.setText(item.title);
      }

      const meta = cardEl.createDiv({ cls: "litradar-card-meta" });
      meta.createSpan({ text: item.journal });
      meta.createSpan({ text: " · " });
      meta.createSpan({ text: articleDate(item) });
      if (typeof result.relevance === "number") {
        meta.createSpan({ text: ` · 相关性 ${result.relevance.toFixed(2)}` });
      }
      if (result.why) {
        cardEl.createDiv({ cls: "litradar-card-why", text: result.why });
      }
      if (Array.isArray(result.topic_tags) && result.topic_tags.length > 0) {
        const tags = cardEl.createDiv({ cls: "litradar-tags" });
        for (const tag of result.topic_tags) tags.createSpan({ cls: "litradar-tag", text: tag });
      }

      const abstract = result.abstract_en || item.abstract_raw;
      if (abstract) {
        const details = cardEl.createEl("details", { cls: "litradar-abstract" });
        details.createEl("summary", {
          text: result.abstract_mode === "verbatim" ? "英文摘要（原文）" : "英文摘要（概述）",
        });
        details.createEl("p", { text: abstract });
      }
      if (item.doi) {
        const doiUrl = safeExternalUrl(`https://doi.org/${item.doi}`);
        if (doiUrl) {
          const doi = cardEl.createEl("a", { cls: "litradar-doi", text: `DOI: ${item.doi}`, href: doiUrl });
          doi.target = "_blank";
          doi.rel = "noopener noreferrer";
        }
      }
    }
  }

  private highlightCards(data: PanelData): HighlightCard[] {
    const byId = new Map(newArticles(data.store).map((item) => [item.id, item]));
    const cards: HighlightCard[] = [];
    for (const result of screenResults(data)) {
      if (result.relevant !== true) continue;
      const item = byId.get(result.id);
      if (item) cards.push({ result, item });
    }
    cards.sort((left, right) => (right.result.relevance ?? 0) - (left.result.relevance ?? 0));
    return cards;
  }

  private renderAllItems(container: HTMLElement, data: PanelData): void {
    const items = newArticles(data.store);
    const details = container.createEl("details", { cls: "litradar-panel litradar-all" });
    details.createEl("summary", { text: `全部条目（${items.length} 篇）` });

    if (items.length === 0) {
      details.createDiv({ cls: "litradar-empty-line", text: this.emptyItemsMessage(data) });
      return;
    }

    const groups = new Map<string, Article[]>();
    for (const item of items) {
      const key = item.journal || "未知期刊";
      const bucket = groups.get(key);
      if (bucket) bucket.push(item);
      else groups.set(key, [item]);
    }

    const byScreen = new Map(screenResults(data).map((result) => [result.id, result]));
    for (const [journal, articles] of groups) {
      const group = details.createDiv({ cls: "litradar-group" });
      group.createEl("h6", { text: `${journal}（${articles.length} 篇）` });
      for (const item of articles) {
        const entry = group.createEl("details", { cls: "litradar-item" });
        const summary = entry.createEl("summary");
        const url = safeExternalUrl(item.url);
        if (url) {
          const title = summary.createEl("a", { cls: "litradar-item-title", text: item.title, href: url });
          title.target = "_blank";
          title.rel = "noopener noreferrer";
          title.addEventListener("click", (event) => event.stopPropagation());
        } else {
          summary.createSpan({ cls: "litradar-item-title", text: item.title });
        }
        summary.createSpan({
          cls: "litradar-item-meta",
          text: ` · ${articleDate(item)}${item.surface ? ` · ${item.surface}` : ""}`,
        });

        const body = entry.createDiv({ cls: "litradar-item-body" });
        const result = byScreen.get(item.id);
        if (result && typeof result.relevance === "number") {
          body.createDiv({
            cls: "litradar-item-relevance",
            text: `相关性 ${result.relevance.toFixed(2)}${result.why ? ` —— ${result.why}` : ""}`,
          });
        }
        body.createDiv({
          cls: "litradar-abstract-label",
          text: item.abstract_raw ? "英文摘要：" : "英文摘要：No abstract supplied by the journal",
        });
        if (item.abstract_raw) body.createEl("p", { cls: "litradar-abstract-text", text: item.abstract_raw });
        if (Array.isArray(item.authors) && item.authors.length > 0) {
          body.createDiv({ cls: "litradar-item-authors", text: `作者：${item.authors.join("，")}` });
        }
        const links = body.createDiv({ cls: "litradar-item-links" });
        if (url) {
          const source = links.createEl("a", { text: "原文", href: url });
          source.target = "_blank";
          source.rel = "noopener noreferrer";
        }
        if (item.doi) {
          const doiUrl = safeExternalUrl(`https://doi.org/${item.doi}`);
          if (doiUrl) {
            const doi = links.createEl("a", { text: `DOI: ${item.doi}`, href: doiUrl });
            doi.target = "_blank";
            doi.rel = "noopener noreferrer";
          }
        }
        if (item.abstract_source) {
          const abstractSource = safeExternalUrl(item.abstract_source);
          if (abstractSource) {
            const link = links.createEl("a", { text: "摘要来源", href: abstractSource });
            link.target = "_blank";
            link.rel = "noopener noreferrer";
          }
        }
        if (item.first_seen_at) {
          links.createSpan({ text: `首次观察：${formatDateTime(item.first_seen_at)}` });
        }
      }
    }
  }

  private emptyItemsMessage(data: PanelData): string {
    if (allFailedOf(data)) {
      // Exit code 4 territory: never claim the day had no new articles.
      return "今日抓取全部失败，无法判断本日是否有新条目。请先修复抓取问题。";
    }
    if (noNewItemsOf(data)) {
      // The only combination allowed to say "nothing new" (03-contracts §2).
      return "所有期刊本轮均无新条目。";
    }
    if (storeScans(data.store).some((scan) => !isHealthy(scan.status))) {
      return "覆盖不完整：部分期刊抓取失败，无法判断本日是否有新条目。";
    }
    return "本日没有待展示的新条目（可能均已报告过）。";
  }

  private renderFailures(container: HTMLElement, data: PanelData): void {
    const scans = storeScans(data.store);
    const failed = scans.filter((scan) => isFailed(scan.status));
    const allFailed = allFailedOf(data) || this.plugin.lastOutcome?.kind === "all-failed";
    if (failed.length === 0 && !allFailed) return;

    const section = container.createEl("section", {
      cls: "litradar-panel litradar-failures",
    });
    section.createEl("h5", {
      text: allFailed ? "今日抓取全部失败" : `抓取异常（${failed.length} 刊）`,
    });
    if (allFailed) {
      section.createDiv({
        cls: "litradar-error-text",
        text: "没有任何期刊成功返回可用内容。本日覆盖不可用，无法判断是否有新条目。",
      });
    }
    if (failed.length > 0) {
      const list = section.createEl("ul", { cls: "litradar-failure-list" });
      for (const scan of failed) {
        const item = list.createEl("li");
        item.createEl("strong", { text: scan.journal });
        item.createSpan({ text: ` · ${scanLabel(scan.status)}` });
        if (scan.http) item.createSpan({ text: ` · HTTP ${scan.http}` });
        if (scan.reason) item.createSpan({ text: ` · ${scan.reason}` });
      }
    }
    const metadataOnly = scans.filter((scan) => scan.status === "metadata-only");
    if (metadataOnly.length > 0) {
      section.createDiv({
        cls: "litradar-warn-text",
        text: `${metadataOnly.map((scan) => scan.journal).join("、")} 仅有元数据；覆盖不完整。`,
      });
    }
  }

  private renderValidation(container: HTMLElement, data: PanelData): void {
    const lines = data.report?.validationLines ?? [];
    const screenOutcome = this.plugin.lastOutcome?.kind === "screen";
    if (lines.length === 0 && !screenOutcome) return;

    const section = container.createEl("section", {
      cls: "litradar-panel litradar-validation",
    });
    section.createEl("h5", { text: `校验记录（${lines.length} 条）` });
    if (lines.length === 0) {
      section.createDiv({
        cls: "litradar-warn-text",
        text: "本轮模型输出在渲染时被降级或拒收；详见日报的「校验记录」。",
      });
    } else {
      const list = section.createEl("ul");
      for (const line of lines.slice(0, 20)) {
        list.createEl("li", { text: line.replace(/^[-*]\s*/, "") });
      }
    }
    const open = section.createEl("button", { cls: "litradar-btn", text: "打开校验记录" });
    open.onclick = () => {
      void this.plugin.openReport(data.activeRunId ?? undefined);
    };
  }

  private renderDoctor(container: HTMLElement): void {
    const doctor = this.plugin.doctor;
    if (!doctor) return;
    const section = container.createEl("section", { cls: "litradar-panel litradar-doctor" });
    section.createEl("h5", { text: "引擎自检" });
    if (doctor.summary) section.createDiv({ cls: "litradar-reason", text: doctor.summary });
    const checks = Array.isArray(doctor.checks) ? doctor.checks : [];
    if (checks.length === 0) {
      section.createDiv({ cls: "litradar-empty-line", text: "自检没有返回明细。" });
      return;
    }
    const list = section.createEl("ul", { cls: "litradar-doctor-list" });
    for (const check of checks) {
      const item = list.createEl("li");
      const mark = check.ok ? "✅" : check.severity === "warn" ? "⚠️" : "❌";
      item.createSpan({ text: `${mark} ` });
      item.createEl("strong", { text: check.name });
      if (check.detail) item.createSpan({ text: ` · ${check.detail}` });
    }
  }

  private renderLog(container: HTMLElement, data: PanelData): void {
    const outcome = this.plugin.lastOutcome;
    const failure = !!outcome && outcome.kind !== "ok" && outcome.kind !== "locked";
    if (!this.plugin.settings.showLogTail && !failure) return;

    const jobLog = this.plugin.job?.stderrTail?.trim();
    const outcomeLog = outcome?.stderrTail?.trim();
    const runLog = data.runLogTail?.trim();
    const text = jobLog || outcomeLog || runLog || "";
    const warnings = data.warnings;

    if (!text && warnings.length === 0) return;

    const details = container.createEl("details", { cls: "litradar-panel litradar-log" });
    details.createEl("summary", { text: "运行日志（末 20 行）" });
    if (text) {
      details.createDiv({
        cls: "litradar-log-label",
        text: jobLog ? "本次运行 stderr" : outcomeLog ? "上次运行 stderr" : "runs/<run_id>/run.log 末尾",
      });
      details.createEl("pre", { cls: "litradar-log-text", text });
    }
    if (warnings.length > 0) {
      const list = details.createEl("ul", { cls: "litradar-warning-list" });
      for (const warning of warnings) list.createEl("li", { text: warning });
    }
  }

  private renderUninitialized(container: HTMLElement, data: PanelData): void {
    const section = container.createEl("section", {
      cls: "litradar-panel litradar-empty",
    });
    section.createEl("h5", { text: "尚未初始化" });
    section.createEl("p", {
      text: `在 vault 根目录下没有找到 ${data.storeDir}/（config.yaml 与 store/ 均不存在）。`,
    });
    const command = `python -m litradar init --vault "${this.plugin.resolvedVaultRoot || "<vault>"}"`;
    section.createEl("pre", { cls: "litradar-command", text: command });
    section.createEl("p", {
      cls: "litradar-muted",
      text: "插件是只读前端，不会自动创建 .litradar。初始化请在上面的命令里完成。",
    });
  }

  private renderNoStore(container: HTMLElement, data: PanelData): void {
    const section = container.createEl("section", {
      cls: "litradar-panel litradar-empty",
    });
    section.createEl("h5", { text: "今日尚未抓取" });
    section.createEl("p", {
      text:
        `没有找到 ${data.storeDir}/store/ 下的抓取结果。` +
        "点击「重抓」，或运行 `python -m litradar fetch`；面板每次轮询都会重新读取目录。",
    });
    if (Object.keys(data.scans).length > 0) {
      section.createEl("p", {
        cls: "litradar-muted",
        text: "state/journal_scans.json 有历史扫描记录，但没有对应的 store，暂不展示。",
      });
    }
  }

  private renderFooter(container: HTMLElement, data: PanelData): void {
    const engine = this.plugin.engine;
    const footer = container.createDiv({ cls: "litradar-footer" });
    footer.setText(
      `${data.storeDir} 只读 · 引擎 ${engine.engine ?? "未检测"}` +
        `${engine.schema ? `（schema ${engine.schema}）` : ""} · 插件 0.1.0`,
    );
  }

  // ----------------------------------------------------------------- helpers

  private banner(
    container: HTMLElement,
    tone: "error" | "warn" | "info" | "muted",
    title: string,
    detail?: string,
    actions: BannerAction[] = [],
    logText?: string,
  ): void {
    const banner = container.createDiv({ cls: `litradar-banner litradar-banner-${tone}` });
    banner.createDiv({ cls: "litradar-banner-title", text: title });
    if (detail) banner.createDiv({ cls: "litradar-banner-detail", text: detail });
    if (logText && logText.trim()) {
      banner.createEl("pre", { cls: "litradar-log-text", text: logText.trim() });
    }
    if (actions.length > 0) {
      const row = banner.createDiv({ cls: "litradar-banner-actions" });
      for (const action of actions) {
        const button = row.createEl("button", { cls: "litradar-btn", text: action.label });
        button.onclick = action.onClick;
      }
    }
  }

  private openSettings(): void {
    const app = this.app as unknown as {
      setting?: { open?: () => void; openTabById?: (id: string) => void };
    };
    app.setting?.open?.();
    app.setting?.openTabById?.(this.plugin.manifest.id);
  }
}

interface BannerAction {
  label: string;
  onClick: () => void;
}

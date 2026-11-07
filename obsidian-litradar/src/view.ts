/**
 * The Litradar panel.
 *
 * Rendering is intentionally dumb: every value shown here was written by the
 * engine (store/<run>.json, runs/<run>/*.json, report.md front-matter). The
 * panel never derives relevance, date windows or Markdown, and it never writes
 * to `.litradar`.
 */

import { articleKey, findReadingEntry, matchesArticle } from "./reading";
import { ItemView, Notice, WorkspaceLeaf } from "obsidian";
import type LitradarPlugin from "./main";
import { ProfileBuilderModal } from "./profileModal";
import { dateSourceNote } from "./store";
import {
  SCHEMA_VERSION,
  type ActionKind,
  type Article,
  type JournalScan,
  type LiveScan,
  type PanelData,
  type ScanStatus,
  type ScreenResult,
  type StorePayload,
} from "./types";

export const VIEW_TYPE_LITRADAR = "litradar-view";

interface ModelPreset {
  label: string;
  model: string;
  baseUrl: string;
}

const MODEL_PRESETS: ModelPreset[] = [
  { label: "DeepSeek Flash（快速）", model: "deepseek-flash", baseUrl: "https://api.deepseek.com" },
  { label: "DeepSeek V4 Pro（推理）", model: "deepseek-v4-pro", baseUrl: "https://api.deepseek.com" },
  { label: "OpenAI GPT-5.1", model: "gpt-5.1", baseUrl: "https://api.openai.com/v1" },
  { label: "OpenAI GPT-4.1", model: "gpt-4.1", baseUrl: "https://api.openai.com/v1" },
  { label: "OpenAI GPT-4.1 mini", model: "gpt-4.1-mini", baseUrl: "https://api.openai.com/v1" },
  { label: "OpenAI GPT-4o", model: "gpt-4o", baseUrl: "https://api.openai.com/v1" },
  { label: "OpenAI GPT-4o mini", model: "gpt-4o-mini", baseUrl: "https://api.openai.com/v1" },
  { label: "自定义…", model: "", baseUrl: "" },
];

const SCAN_LABELS: Record<string, string> = {
  ok: "✅ 已读",
  "metadata-only": "⚠ 仅元数据",
  "off-cycle": "🛌 非出版日",
  blocked: "⛔ 被拦截",
  timeout: "⏱ 超时",
  error: "❌ 解析失败",
  running: "⏳ 进行中",
};

const FAILED_STATUSES: ScanStatus[] = ["blocked", "timeout", "error"];

function isHealthy(status: string | undefined): boolean {
  return status === "ok" || status === "metadata-only" || status === "off-cycle";
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

function formatMonthDayTime(iso: string | null | undefined): string {
  const parsed = parseDate(iso);
  if (!parsed) return "—";
  return `${pad2(parsed.getMonth() + 1)}-${pad2(parsed.getDate())} ${formatTime(iso)}`;
}

const WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/** "每日" / "每周四" — the cadence comes from journals/<slug>.yaml. */
function cadenceLabel(
  scan: Partial<Pick<JournalScan, "cadence" | "weekday">> | undefined,
): string {
  if (!scan) return "—";
  if (scan.cadence === "weekly" && typeof scan.weekday === "number") {
    return `每周${WEEKDAY_LABELS[scan.weekday] ?? ""}`;
  }
  return "每日";
}

/** "Vol 189 · No 22" or a cover date; em dash when the source omits it. */
function issueLabel(scan: Partial<JournalScan> | undefined): string {
  if (!scan) return "—";
  const parts: string[] = [];
  if (scan.volume) parts.push(`Vol ${scan.volume}`);
  if (scan.issue) parts.push(`No ${scan.issue}`);
  if (parts.length > 0) return parts.join(" · ");
  if (scan.cover_date) return String(scan.cover_date);
  if (scan.surface && /online|latest|advance|first/i.test(scan.surface)) return "在线优先";
  return "—";
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

function isPreprint(item: Article): boolean {
  if (item.extra && (item.extra.preprint || item.extra.server)) return true;
  return /^(bioRxiv|medRxiv|arXiv|SSRN|Research Square)$/i.test(item.journal || "");
}

function articleTypeLabel(item: Article): string {
  if (item.article_type && item.article_type.trim()) return item.article_type.trim();
  if (isPreprint(item)) return "Preprint";
  if (item.content_type === "review") return "Review";
  if (item.content_type === "research-article") return "Research Article";
  return "Other";
}

function articleSubject(item: Article): string {
  const value = item.extra?.category ?? item.extra?.subject;
  return typeof value === "string" ? value.trim() : "";
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
  private selectedRunId: string | null = null;
  private archiveDays = 7;
  private archiveItems: Article[] = [];
  private archiveQuery = "";
  private archiveStatus = "";
  private archiveJournal = "";
  private archiveKind = "";
  private archiveTopic = "";
  private archiveLimit = 50;
  private archiveLoading = false;
  private query = "";
  private journal = "";
  private articleKind = "";
  private relevantOnly = false;
  private libraryTab = "todo";
  private searchTimer: number | null = null;

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
    if (this.searchTimer !== null) window.clearTimeout(this.searchTimer);
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
      const data = await this.plugin.reader.loadPanelData(this.plugin.today(), this.selectedRunId);
      if (this.closed) return;
      const beforeZotero = JSON.stringify([this.plugin.zoteroLibrary, this.plugin.zoteroStatuses, this.plugin.zoteroLegacyCount, this.plugin.zoteroError]);
      await this.plugin.refreshZoteroReading();
      if (this.closed) return;
      const changed = JSON.stringify(data) !== JSON.stringify(this.snapshot) ||
        beforeZotero !== JSON.stringify([this.plugin.zoteroLibrary, this.plugin.zoteroStatuses, this.plugin.zoteroLegacyCount, this.plugin.zoteroError]);
      this.snapshot = data;
      if (changed) this.render();
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
        schedule: {
          kind: "daily",
          time: "09:00",
          tz: "Asia/Shanghai",
          installed: false,
          nextRunAt: null,
          lastScanAt: null,
          todayFetched: false,
        },
        profile: {
          direction: "",
          keywords: [],
          sources: [],
          model: "",
          updatedAt: null,
          fromZotero: false,
        },
        llm: { model: "deepseek-chat", baseUrl: "https://api.deepseek.com" },
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
    const scrollTop = container.scrollTop;
    const scrollLeft = container.scrollLeft;
    const expanded = new Map<string, boolean>();
    const detailKey = (el: HTMLDetailsElement): string =>
      el.dataset.stateKey ?? `${el.className}:${el.querySelector(":scope > summary")?.textContent?.replace(/（\d+ 篇）/g, "")}`;
    container.querySelectorAll<HTMLDetailsElement>("details").forEach(el => {
      expanded.set(detailKey(el), el.open);
    });
    // Keep drafts and keyboard focus when job events rebuild the panel.
    const selector = "input, textarea, select";
    const controls = Array.from(container.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector));
    const controlKeys = (elements: typeof controls): string[] => {
      const counts = new Map<string, number>();
      return elements.map(el => {
        const base = `${el.tagName}:${el.className}:${el.dataset.articleKey ?? ""}`;
        const count = counts.get(base) ?? 0;
        counts.set(base, count + 1);
        return `${base}:${count}`;
      });
    };
    const keys = controlKeys(controls);
    const activeIndex = controls.indexOf(document.activeElement as HTMLInputElement);
    const drafts = new Map(controls.map((el, index) => [keys[index], ({
      value: el.value,
      dirty: !(el instanceof HTMLSelectElement) && el.value !== el.defaultValue,
      checked: el instanceof HTMLInputElement ? el.checked : false,
    })]));
    const active = controls[activeIndex];
    const selection = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement
      ? [active.selectionStart, active.selectionEnd] : null;
    this.renderContent();
    container.querySelectorAll<HTMLDetailsElement>("details").forEach(el => {
      const open = expanded.get(detailKey(el));
      if (open !== undefined) el.open = open;
    });
    const nextControls = Array.from(container.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector));
    const nextKeys = controlKeys(nextControls);
    nextControls.forEach((el, index) => {
      const draft = drafts.get(nextKeys[index]);
      if (!draft) return;
      if (draft.dirty && !el.classList.contains("litradar-article-search")) el.value = draft.value;
      if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
        el.checked = draft.checked;
      }
      if (nextKeys[index] === keys[activeIndex]) {
        el.focus({ preventScroll: true });
        if (selection && selection[0] !== null && selection[1] !== null &&
            (el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && /^(text|search|url|tel|password)$/.test(el.type)))) {
          el.setSelectionRange(selection[0], selection[1]);
        }
      }
    });
    container.scrollTop = scrollTop;
    container.scrollLeft = scrollLeft;
  }

  private renderContent(): void {
    const container = this.contentEl;
    container.empty();
    container.addClass("litradar-view");

    const data = this.snapshot;
    this.renderHeader(container, data);
    this.renderBanners(container, data);
    if (!this.selectedRunId) {
      this.renderSchedule(container, data);
      this.renderToolbar(container, data);
    }
    if (data) this.renderHistory(container, data);
    this.renderLibrary(container);

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

    this.renderArchive(container);
    this.renderFilters(container, data);
    this.renderHighlights(container, data);
    this.renderAllItems(container, data);
    this.renderCoverage(container, data);
    this.renderResearch(container, data);
    this.renderFailures(container, data);
    this.renderValidation(container, data);
    this.renderDoctor(container);
    this.renderLog(container, data);
    this.renderFooter(container, data);
  }

  private async loadArchive(): Promise<void> {
    this.archiveLoading = true;
    this.render();
    try { this.archiveItems = await this.plugin.reader.readArchive(this.archiveDays); }
    catch (error) { new Notice(`文献库读取失败：${error}`); }
    finally { this.archiveLoading = false; this.archiveLimit = 50; this.render(); }
  }

  private renderArchive(container: HTMLElement): void {
    const section = container.createEl("details", {cls: "litradar-panel"});
    section.dataset.stateKey = "cross-batch-library";
    section.createEl("summary", {text: `跨批次文献库 · ${this.archiveItems.length} 篇`});
    const controls = section.createDiv({cls: "litradar-reading-controls"});
    const range = controls.createEl("select", {attr: {"aria-label": "文献库日期范围"}});
    for (const [value, label] of [[7,"最近一周"],[30,"最近一个月"],[0,"全部历史"]] as const) range.createEl("option", {value: String(value), text: label});
    range.value = String(this.archiveDays);
    range.onchange = () => {this.archiveDays = Number(range.value); void this.loadArchive();};
    const load = controls.createEl("button", {text: this.archiveLoading ? "读取中…" : "加载／刷新文献库"});
    load.disabled = this.archiveLoading;
    load.onclick = () => void this.loadArchive();
    const search = controls.createEl("input", {type:"search", value:this.archiveQuery, placeholder:"搜索中文、英文、摘要或 DOI", attr:{"aria-label":"搜索全部历史文献"}});
    search.oninput = () => {this.archiveQuery=search.value; if (this.searchTimer) window.clearTimeout(this.searchTimer); this.searchTimer=window.setTimeout(()=>this.render(),200);};
    const dropdown = (label: string, options: [string,string][], value: string, change: (v:string)=>void) => {
      const el=controls.createEl("select", {attr:{"aria-label":label}});
      for (const [v,t] of options) el.createEl("option",{value:v,text:t});
      el.value=value; el.onchange=()=>{change(el.value);this.render();};
    };
    dropdown("文献库期刊", [["","全部期刊"], ...Array.from(new Set(this.archiveItems.map(a=>a.journal))).sort().map(j=>[j,j] as [string,string])],this.archiveJournal,v=>this.archiveJournal=v);
    dropdown("文献库类型", [["","全部类型"],["journal","期刊"],["preprint","预印本"]],this.archiveKind,v=>this.archiveKind=v);
    const topics=Array.from(new Set(this.archiveItems.flatMap(a=>Array.isArray(a.extra?.tags_auto) ? a.extra.tags_auto as string[] : []))).sort();
    dropdown("文献库主题", [["","全部主题"],...topics.map(t=>[t,t] as [string,string])],this.archiveTopic,v=>this.archiveTopic=v);
    dropdown("文献库阅读状态", [["","全部阅读状态"],["none","尚未设置"], ...this.plugin.zoteroStatuses.map(o=>[o.value,o.label] as [string,string])], this.archiveStatus,v=>this.archiveStatus=v);
    const matches=this.archiveItems.filter(item=>matchesArticle(item,{query:this.archiveQuery,journal:this.archiveJournal,kind:this.archiveKind,relevantOnly:false},isPreprint(item),true) && (!this.archiveTopic || (item.extra?.tags_auto as string[] || []).includes(this.archiveTopic)) && (!this.archiveStatus || (findReadingEntry(this.plugin.zoteroLibrary,item)?.status || "none")===this.archiveStatus));
    section.createEl("p",{text:`匹配 ${matches.length} 篇 · 按日报日期筛选，DOI 去重；阅读状态来自 Zotero`});
    for (const item of matches.slice(0,this.archiveLimit)) {
      const card=section.createEl("details",{cls:"litradar-card"}); card.dataset.stateKey=`archive:${item.id}`;
      card.createEl("summary",{text:item.title_zh || item.title});
      card.createEl("p",{text:`${item.journal} · ${item.online_date || "日期待核验"} · 出现于 ${(item.extra?.report_dates as string[] || []).join("、")}`});
      if(item.title_zh) card.createEl("p",{text:item.title});
      if(item.summary_zh) card.createEl("p",{text:item.summary_zh});
      if(item.abstract_raw) card.createEl("p",{text:item.abstract_raw});
      this.renderReadingAction(card,item,String(item.extra?.report_date || ""));
    }
    if(matches.length>this.archiveLimit) {const more=section.createEl("button",{text:"再显示 50 篇"});more.onclick=()=>{this.archiveLimit+=50;this.render();};}
  }

  private hasFilters(): boolean {
    return !!(this.query.trim() || this.journal || this.articleKind || this.relevantOnly);
  }

  private articleMatches(item: Article, data: PanelData): boolean {
    return matchesArticle(item, {
      query: this.query, journal: this.journal, kind: this.articleKind, relevantOnly: this.relevantOnly,
    }, isPreprint(item), screenResults(data).some(row => row.id === item.id && row.relevant === true));
  }

  private renderHistory(container: HTMLElement, data: PanelData): void {
    const row = container.createDiv({ cls: "litradar-reading-controls" });
    const label = row.createEl("label", { text: "浏览批次 " });
    const select = label.createEl("select", { cls: "litradar-history-select" });
    select.createEl("option", { value: "", text: "最新批次（自动跟随）" });
    for (const id of [...data.runIds].reverse()) select.createEl("option", { value: id, text: id });
    select.value = this.selectedRunId ?? "";
    select.onchange = () => {
      this.selectedRunId = select.value || null;
      this.journal = "";
      void this.refresh();
    };
    if (data.activeRunId) {
      const open = row.createEl("button", { text: "打开本批次日报" });
      open.onclick = () => void this.plugin.openReport(data.activeRunId ?? undefined);
    }
    row.createEl("small", { text: "选择历史批次仅用于浏览；切回“最新批次”可进行抓取和重生成。" });
  }

  private renderReadingAction(container: HTMLElement, article: Article, collectionDate?: string): void {
    const actions = container.createDiv({ cls: "litradar-zotero-actions" });
    const label = actions.createEl("label", { cls: "litradar-reading-action", text: "Zotero 阅读状态 " });
    const feedback = actions.createEl("select", {attr:{"aria-label":"推荐反馈"}});
    for (const [value,text] of [["","推荐反馈…"],["relevant","相关"],["irrelevant","不相关"],["less","减少这类推荐"],["clear","撤销反馈"]]) feedback.createEl("option",{value,text});
    feedback.onchange=async()=>{if(!feedback.value)return;feedback.disabled=true;try{await this.plugin.saveRecommendationFeedback(article,feedback.value);}catch(error){new Notice(String(error));}finally{feedback.disabled=false;feedback.value="";}};
    const state = this.plugin.articleStates?.get(article.doi || article.id);
    const known = findReadingEntry(this.plugin.zoteroLibrary, article);
    actions.createEl("small", {text: state ? `${state.phase}${state.error ? " · "+state.error : ""}` : known ? "已在 Zotero · PDF 状态待检查" : "入库与 PDF 状态尚未检查"});
    const inspect = actions.createEl("button", {text:"刷新入库／PDF 状态"});
    inspect.onclick=()=>void this.plugin.inspectZoteroArticle(article);
    if(article.title_zh) container.createEl("p",{cls:"litradar-card-meta",text:`原文：${article.title}`});
    if(article.summary_zh) container.createEl("p",{text:article.summary_zh});
    if(article.why_zh) container.createEl("p",{text:`推荐理由：${article.why_zh}`});
    if(article.extra?.late_arrival) container.createEl("p",{text:"补收录：来源延迟登记，保留原发表日期"});
    if(article.extra?.feedback_reason) container.createEl("p",{text:`推荐调整：${article.extra.feedback_reason}`});
    if(article.extra?.superseded_by) container.createEl("p",{text:"已有正式版收录，预印本保留原阅读记录，不再重复置顶"});
    const versions = article.extra?.versions;
    if(Array.isArray(versions)) for(const version of versions) {
      const doi=String(version.doi || "");
      if(!/^10\.\d{4,9}\/[^\s<>]+$/.test(doi))continue;
      const row=container.createEl("p");
      row.createEl("a",{text:`关联版本：${doi}`,href:`https://doi.org/${encodeURI(doi)}`,attr:{target:"_blank",rel:"noopener noreferrer"}});
      row.createSpan({text:` · ${version.evidence || "DOI 关系"}`});
      const related = this.plugin.zoteroLibrary.find(entry=>(entry.article.doi || "").toLowerCase()===doi.toLowerCase());
      if(related) row.createSpan({text:` · 原版本阅读状态：${related.status || "未设置"}（原记录保留）`});
    }
    const select = label.createEl("select");
    select.dataset.articleKey = articleKey(article);
    select.createEl("option", { value: "", text: "未设置" });
    for (const option of this.plugin.zoteroStatuses) select.createEl("option", { value: option.value, text: option.label });
    const status = findReadingEntry(this.plugin.zoteroLibrary, article)?.status ?? "";
    if (status && !this.plugin.zoteroStatuses.some(option => option.value === status)) {
      select.createEl("option", { value: status, text: `${status}（旧状态）` });
    }
    select.value = status;
    select.disabled = !!this.plugin.zoteroError || !this.plugin.zoteroStatuses.length;
    for (const [action, title] of [["save", "保存到 Zotero"], ["download", "下载 PDF"], ["open", "在 Zotero 打开"], ["pdf", "打开 PDF"]] as const) {
      const button = actions.createEl("button", { cls: "litradar-zotero-action", text: title });
      button.onclick = async () => {
        button.disabled = true;
        try { await this.plugin.zoteroArticleAction(article, action, collectionDate); }
        catch (error) { new Notice(error instanceof Error ? error.message : String(error), 10000); }
        finally { button.disabled = false; }
      };
    }
    select.onchange = async () => {
      select.disabled = true;
      try {
        await this.plugin.setReadingStatus(article, select.value, collectionDate);
      } catch (error) {
        new Notice(`阅读状态保存失败：${error instanceof Error ? error.message : String(error)}`);
        this.render();
      } finally { select.disabled = false; }
    };
  }

  private renderLibrary(container: HTMLElement): void {
    const entries = this.plugin.zoteroLibrary;
    const details = container.createEl("details", { cls: "litradar-panel litradar-library" });
    details.dataset.stateKey = "reading-library";
    details.createEl("summary", { text: `Reading List · ${entries.length} 篇` });
    details.createEl("p", { cls: "litradar-card-meta", text: "状态与 Zotero Reading List 共享，支持其自定义状态；可手动刷新同步。" });
    const refresh = details.createEl("button", { text: "刷新 Zotero" });
    refresh.onclick = async () => { refresh.disabled = true; await this.plugin.refreshZoteroReading(true); this.render(); };
    if (this.plugin.zoteroError) {
      details.open = true;
      details.createEl("p", { cls: "litradar-error-text", text: this.plugin.zoteroError + "；当前清单可能不是最新状态。" });
    }
    const legacyCount = Object.keys(this.plugin.settings.readingLibrary).length;
    if (legacyCount || this.plugin.zoteroLegacyCount) {
      for (const [key, title] of [["readingListTodoStatus", "旧待读映射"], ["readingListReadStatus", "旧已读映射"]] as const) {
        const label = details.createEl("label", { cls: "litradar-reading-action", text: title });
        const mapping = label.createEl("select");
        mapping.createEl("option", {value:"",text:"请选择 Reading List 状态"});
        for (const option of this.plugin.zoteroStatuses) mapping.createEl("option", {value:option.value,text:option.label});
        mapping.value = this.plugin.settings[key];
        mapping.onchange = () => { void this.plugin.updateSettings({[key]:mapping.value}); };
      }
    }
    if (this.plugin.zoteroLegacyCount) {
      const migrate = details.createEl("button", {text:`迁移旧 Zotero 标签（${this.plugin.zoteroLegacyCount} 篇）`});
      migrate.onclick = async () => {
        migrate.disabled = true;
        try {
          const result = await this.plugin.migrateZoteroTags();
          new Notice(`迁移 ${result.migrated} 篇，保留已有状态 ${result.preserved} 篇，失败 ${result.failed.length} 篇`, 10000);
        } catch(error) { new Notice(error instanceof Error ? error.message : String(error), 10000); }
        finally { this.render(); }
      };
    }
    if (legacyCount) {
      const migrate = details.createEl("button", { text: `迁移旧本地清单到 Zotero（${legacyCount} 篇）` });
      migrate.onclick = async () => {
        migrate.disabled = true;
        try { await this.plugin.migrateReadingLibrary(); new Notice("旧清单已迁移到 Zotero"); }
        catch (error) { new Notice(`迁移未完成，未迁移条目已保留：${error instanceof Error ? error.message : String(error)}`, 10000); }
        finally { this.render(); }
      };
    }
    const select = details.createEl("select", { cls: "litradar-library-tab", attr: { "aria-label": "阅读清单状态" } });
    select.createEl("option", { value: "", text: "全部状态" });
    for (const option of this.plugin.zoteroStatuses) select.createEl("option", {value:option.value,text:option.label});
    if (this.libraryTab && !this.plugin.zoteroStatuses.some(option => option.value === this.libraryTab)) this.libraryTab = "";
    select.value = this.libraryTab;
    select.onchange = () => { this.libraryTab = select.value; this.render(); };
    const rows = entries.filter(row => !this.libraryTab || row.status === this.libraryTab).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    if (!rows.length) details.createEl("p", { text: "清单为空。选择 Reading List 状态后即可入库，并与 Zotero 的状态列同步。" });
    for (const row of rows) {
      const card = details.createDiv({ cls: "litradar-card" });
      const url = safeExternalUrl(row.article.url);
      if (url) card.createEl("a", { text: row.article.title, href: url, attr: { target: "_blank", rel: "noopener noreferrer" } });
      else card.createEl("strong", { text: row.article.title });
      card.createDiv({ cls: "litradar-card-meta", text: `${row.article.journal || "未知期刊"} · ${row.status}` });
      if (row.article.abstract_raw) {
        const abstract = card.createEl("details");
        abstract.dataset.stateKey = `library-abstract:${articleKey(row.article)}`;
        abstract.createEl("summary", { text: "摘要" });
        abstract.createEl("p", { text: row.article.abstract_raw });
      }
      this.renderReadingAction(card, row.article);
    }
  }

  private renderFilters(container: HTMLElement, data: PanelData): void {
    const row = container.createDiv({ cls: "litradar-reading-controls" });
    const search = row.createEl("input", {
      type: "search", cls: "litradar-article-search", value: this.query,
      placeholder: "搜索标题、摘要、作者或 DOI…", attr: { "aria-label": "搜索本批次文献" },
    });
    search.oninput = () => {
      this.query = search.value;
      if (this.searchTimer !== null) window.clearTimeout(this.searchTimer);
      this.searchTimer = window.setTimeout(() => { this.searchTimer = null; this.render(); }, 200);
    };
    const dropdown = (cls: string, label: string, options: string[][], value: string, change: (value: string) => void) => {
      const select = row.createEl("select", { cls, attr: { "aria-label": label } });
      for (const [value, text] of options) select.createEl("option", { value, text });
      select.value = value;
      select.onchange = () => { change(select.value); this.render(); };
    };
    dropdown("litradar-journal-filter", "期刊", [["", "全部期刊"], ...Array.from(new Set(newArticles(data.store).map(item => item.journal))).sort().map(name => [name, name])], this.journal, value => { this.journal = value; });
    dropdown("litradar-kind-filter", "文献类型", [["", "全部类型"], ["journal", "期刊文章"], ["preprint", "预印本"]], this.articleKind, value => { this.articleKind = value; });
    dropdown("litradar-relevance-filter", "相关性", [["", "全部相关性"], ["relevant", "仅模型判定相关"]], this.relevantOnly ? "relevant" : "", value => { this.relevantOnly = !!value; });
    const clear = row.createEl("button", { text: "清除筛选" });
    clear.onclick = () => { this.query = this.journal = this.articleKind = ""; this.relevantOnly = false; this.render(); };
    const items = newArticles(data.store);
    row.createEl("small", { text: `本批次新条目 ${items.filter(item => this.articleMatches(item, data)).length} / ${items.length} 篇 · 筛选不影响独立阅读清单` });
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
      status.setText(`今日尚未抓取 · 计划 ${data.schedule.time}`);
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
    const when = data.fetchedToday ? "今日已抓取" : "上次抓取";
    status.setText(
      `${icon} ${when} ${formatTime(data.store.scan_at)} · 覆盖 ${coverage} · ${items} 篇 / 置顶 ${highlights}`,
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

    if (data?.store && typeof data.store.schema === "number" && data.store.schema > SCHEMA_VERSION) {
      this.banner(
        container,
        "warn",
        "数据结构版本高于插件",
        `store schema=${data.store.schema}（插件支持 ${SCHEMA_VERSION}）。请升级插件，不要修改 store。`,
      );
    }
  }

  private renderSchedule(container: HTMLElement, data: PanelData | null): void {
    if (!data || data.error) return;
    const schedule = data.schedule;
    const section = container.createEl("section", { cls: "litradar-panel litradar-schedule" });
    const head = section.createDiv({ cls: "litradar-schedule-head" });
    const title = head.createDiv({ cls: "litradar-schedule-title" });
    title.createEl("h5", { text: "抓取计划" });
    title.createDiv({
      cls: "litradar-schedule-sub",
      text: `${schedule.kind === "weekly" ? "每周" : "每天"} ${schedule.time} · ${schedule.tz}`,
    });

    const badge = head.createDiv({
      cls: `litradar-pill ${schedule.todayFetched ? "is-ok" : "is-warn"}`,
      text: schedule.todayFetched
        ? `今日已抓取 ${formatTime(schedule.lastScanAt)}`
        : "今日尚未抓取",
    });
    badge.title = schedule.lastScanAt
      ? `最近一次抓取：${formatDateTime(schedule.lastScanAt)}`
      : "还没有任何抓取记录";

    const grid = section.createDiv({ cls: "litradar-schedule-grid" });
    const cell = (label: string, value: string, cls = ""): void => {
      const item = grid.createDiv({ cls: `litradar-schedule-cell ${cls}`.trim() });
      item.createDiv({ cls: "litradar-schedule-label", text: label });
      item.createDiv({ cls: "litradar-schedule-value", text: value });
    };
    cell("下次计划", !schedule.installed && !this.plugin.settings.autoRunEnabled ? "未启用" : schedule.nextRunAt ? formatMonthDayTime(schedule.nextRunAt) : "—");
    cell(
      "系统后台定时",
      schedule.installed ? "已安装" : "未安装",
      schedule.installed ? "" : "is-warn",
    );
    cell("上次抓取", formatDateTime(schedule.lastScanAt));
    cell("今日日报", data.report ? "已生成" : "未生成", data.report ? "" : "is-warn");
    cell("Obsidian 自动抓取", this.plugin.settings.autoRunEnabled ? "已开启" : "已关闭");
    section.createDiv({
      cls: "litradar-schedule-hint",
      text: this.plugin.settings.autoRunEnabled
        ? "Obsidian 内自动抓取已开启，保持应用运行即可按计划检查。系统后台定时单独管理。"
        : schedule.installed ? "Obsidian 内自动抓取已关闭；系统后台任务仍独立运行。" : "自动抓取已关闭。可在“自动抓取设置”中开启并修改每日时间。",
    });

    const actions = section.createDiv({ cls: "litradar-schedule-actions" });
    const settings = actions.createEl("button", {cls:"litradar-btn",text:"自动抓取设置"});
    settings.onclick = () => this.openSettings();
    const busy = !!this.plugin.job;
    const blocked = !!this.plugin.engineBlocker();
    const addButton = (label: string, action: ActionKind, primary = false): void => {
      const button = actions.createEl("button", {
        cls: `litradar-btn${primary ? " litradar-btn-primary" : ""}`,
        text: label,
      });
      button.disabled = busy || blocked;
      button.onclick = () => void this.plugin.runAction(action, action === "repair" ? {runId:data?.activeRunId || undefined} : undefined);
    };

    if (!schedule.todayFetched) {
      addButton("立即抓取并生成", "run", true);
    } else if (!data.report) {
      addButton("生成今日日报", "render", true);
    } else {
      addButton("重新抓取并生成", "run");
    }
    if (busy) {
      const cancel = actions.createEl("button", {
        cls: "litradar-btn litradar-btn-danger",
        text: "取消",
      });
      cancel.onclick = () => this.plugin.cancelJob();
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
        void this.plugin.runAction(action, action === "repair" ? {runId:data?.activeRunId || undefined} : undefined);
      };
    };

    add("抓取并生成", "run", !engineBlocked && !busy && initialized, reason("尚未初始化"));
    add("只抓取", "fetch", !engineBlocked && !busy && initialized, reason("尚未初始化"));
    add("重试失败期刊", "repair", !engineBlocked && !busy && hasStore, reason("尚无批次"));
    add("补齐／回查迟到", "maintain", !engineBlocked && !busy && initialized, reason());
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

  private renderResearch(container: HTMLElement, data: PanelData | null): void {
    if (!data) return;
    const section = container.createEl("section", { cls: "litradar-panel litradar-research" });
    const head = section.createDiv({ cls: "litradar-research-head" });
    const title = head.createDiv();
    title.createEl("h5", { text: "科研关键词" });
    title.createDiv({
      cls: "litradar-research-sub",
      text: data.profile.fromZotero
        ? `来自 Zotero${data.profile.sources.length ? " · " + data.profile.sources.join("、") : ""}` +
          (data.profile.updatedAt ? ` · ${data.profile.updatedAt.slice(0, 10)}` : "")
        : "当前来自 config.yaml；尚未从 Zotero 生成",
    });
    const build = head.createEl("button", { cls: "litradar-btn", text: "从 Zotero 生成" });
    build.onclick = () => {
      new ProfileBuilderModal(this.app, this.plugin, () => void this.refresh()).open();
    };

    section.createDiv({
      cls: "litradar-profile-direction",
      text: data.profile.direction || "尚未设置研究方向（direction）",
    });

    const keywords = data.profile.keywords;
    const chipBox = section.createDiv({ cls: "litradar-keywords" });
    if (keywords.length === 0) {
      chipBox.createSpan({ cls: "litradar-empty-line", text: "还没有关键词，点击右上角从 Zotero 生成。" });
    } else {
      for (const keyword of keywords.slice(0, 20)) {
        chipBox.createSpan({ cls: "litradar-keyword", text: keyword });
      }
      if (keywords.length > 20) {
        chipBox.createSpan({ cls: "litradar-empty-line", text: `+${keywords.length - 20}` });
      }
    }

    const divider = section.createDiv({ cls: "litradar-research-divider" });
    divider.createEl("h5", { text: "生成模型" });
    const note = section.createDiv({
      cls: "litradar-research-sub",
      text: "只改模型和 base URL；API key 用 llm.api_key_file 指向 vault 外文件（推荐），或写进 config.local.yaml。",
    });
    note.addClass("litradar-model-note");

    const row = section.createDiv({ cls: "litradar-model-row" });
    const select = row.createEl("select", { cls: "litradar-model-select" });
    const current = data.llm;
    let selected = MODEL_PRESETS.findIndex(
      (preset) => preset.model === current.model && preset.baseUrl === current.baseUrl,
    );
    if (selected < 0) selected = MODEL_PRESETS.length - 1;
    MODEL_PRESETS.forEach((preset, index) => {
      const option = select.createEl("option", { text: preset.label, value: String(index) });
      if (index === selected) option.selected = true;
    });

    const modelInput = row.createEl("input", {
      type: "text",
      cls: "litradar-model-input",
      value: current.model,
      placeholder: "模型 ID",
    });
    const baseInput = row.createEl("input", {
      type: "text",
      cls: "litradar-model-base",
      value: current.baseUrl,
      placeholder: "https://api.example.com/v1",
    });
    select.addEventListener("change", () => {
      const preset = MODEL_PRESETS[Number(select.value)];
      if (preset && preset.model) {
        modelInput.value = preset.model;
        baseInput.value = preset.baseUrl;
      }
    });

    const saveRow = section.createDiv({ cls: "litradar-model-actions" });
    const status = saveRow.createSpan({ cls: "litradar-model-status" });
    const save = saveRow.createEl("button", { cls: "litradar-btn", text: "保存模型设置" });
    save.onclick = async () => {
      const model = modelInput.value.trim();
      const baseUrl = baseInput.value.trim();
      if (!model || !baseUrl) {
        status.setText("模型 ID 和 base URL 不能为空");
        return;
      }
      save.disabled = true;
      status.setText("保存中…");
      const modelOk = await this.plugin.engineConfigSet("llm.model", model);
      const baseOk = modelOk ? await this.plugin.engineConfigSet("llm.base_url", baseUrl) : false;
      save.disabled = false;
      if (modelOk && baseOk) {
        status.setText("已保存，下次抓取生效");
        new Notice("模型设置已保存");
        void this.refresh();
      } else {
        status.setText("保存失败，请查看面板日志");
      }
    };
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

    const metaFor = (name: string): Partial<JournalScan> => {
      const stored = storeRows.find((scan) => scan.journal === name);
      const history = data.scans[name];
      return {
        cadence: stored?.cadence ?? history?.cadence,
        weekday: stored?.weekday ?? history?.weekday,
        volume: stored?.volume ?? history?.volume,
        issue: stored?.issue ?? history?.issue,
        issue_identifier: stored?.issue_identifier ?? history?.issue_identifier,
        cover_date: stored?.cover_date ?? history?.cover_date,
        surface: stored?.surface ?? history?.surface,
      };
    };
    const weeklyCount = names.filter((name) => metaFor(name).cadence === "weekly").length;
    const dailyCount = names.length - weeklyCount;
    const issueCount = names.filter((name) => issueLabel(metaFor(name)) !== "—").length;
    section.createDiv({
      cls: "litradar-coverage-summary",
      text: `日更 ${dailyCount} 本 · 周更 ${weeklyCount} 本 · 已识别本期 ${issueCount} 本`,
    });

    const tableWrap = section.createDiv({ cls: "litradar-table-wrap" });
    const table = tableWrap.createEl("table", { cls: "litradar-table" });
    const head = table.createEl("thead").createEl("tr");
    for (const title of ["期刊", "更新", "本期", "状态", "条数", "上次成功"]) {
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
      const meta = metaFor(name);
      const row = body.createEl("tr");
      if (isFailed(status)) {
        failedCount += 1;
        row.addClass("litradar-row-fail");
      }

      const journalCell = row.createEl("td");
      journalCell.createEl("div", { cls: "litradar-journal-name", text: name });
      if (stored?.surface) {
        journalCell.createDiv({ cls: "litradar-journal-surface", text: stored.surface });
      }
      if (reason) journalCell.createEl("div", { cls: "litradar-reason", text: String(reason) });
      if ((history?.consecutive_failures ?? 0) >= 3) {
        journalCell.createEl("div", {
          cls: "litradar-reason litradar-reason-bad",
          text: `连续失败 ${history?.consecutive_failures} 次`,
        });
      }

      const cadenceCell = row.createEl("td", {
        cls: `litradar-cadence ${meta.cadence === "weekly" ? "is-weekly" : "is-daily"}`,
        text: cadenceLabel(meta),
      });
      cadenceCell.title =
        meta.cadence === "weekly"
          ? "周更期刊：非出版日会显示 off-cycle，不算失败"
          : "日更/连续出版：每个工作日检查";
      row.createEl("td", { cls: "litradar-issue", text: issueLabel(meta) });

      const statusCell = row.createEl("td", {
        text: scanLabel(status),
        cls: `litradar-scan litradar-scan-${status}`,
      });
      if (liveScan) statusCell.addClass("is-live");
      if (isFailed(status) || status === "metadata-only") {
        const retry=journalCell.createEl("button",{text:"只重试本刊"});
        retry.disabled=!!this.plugin.job;
        retry.onclick=()=>void this.plugin.runAction("repair",{runId:data.activeRunId || undefined,journal:name});
      }
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
    const cards = this.highlightCards(data).filter(card => this.articleMatches(card.item, data));
    const cap = data.store?.items?.some(item=>typeof item.extra?.highlight === "boolean") ? cards.length : data.report?.highlights ?? 8;
    section.createEl("h5", {
      text: `与你最相关（${Math.min(cards.length, Math.max(0, cap))} 篇）`,
    });

    if (cards.length === 0) {
      let message: string;
      if (this.hasFilters()) {
        message = "没有符合筛选条件的推荐文献，试试清除筛选或浏览全部条目。";
      } else if (!data.screen) {
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
      this.renderReadingAction(cardEl, item, data.store?.window?.end || data.activeRunId || undefined);
      const titleRow = cardEl.createDiv({ cls: "litradar-card-title" });
      const url = safeExternalUrl(item.url);
      if (url) {
        const link = titleRow.createEl("a", { text: item.title_zh || item.title, href: url });
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      } else {
        titleRow.setText(item.title_zh || item.title);
      }

      const meta = cardEl.createDiv({ cls: "litradar-card-meta" });
      meta.createSpan({ text: item.journal });
      meta.createSpan({ text: " · " });
      meta.createSpan({ cls: "litradar-card-type", text: articleTypeLabel(item) });
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
        details.dataset.stateKey = `abstract:${item.id}`;
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
    if ([...byId.values()].some(item => typeof item.extra?.highlight === "boolean")) {
      return [...byId.values()].filter(item=>item.extra?.highlight === true && !item.extra?.superseded_by).map(item=>({item,result:{id:item.id,relevant:true,relevance:Number(item.extra?.rank_score || 0),why:item.why_zh}})).sort((a,b)=>(b.result.relevance || 0)-(a.result.relevance || 0));
    }
    for (const result of screenResults(data)) {
      if (result.relevant !== true) continue;
      const item = byId.get(result.id);
      if (item) cards.push({ result, item });
    }
    cards.sort((left, right) => (right.result.relevance ?? 0) - (left.result.relevance ?? 0));
    return cards;
  }

  private renderAllItems(container: HTMLElement, data: PanelData): void {
    const items = newArticles(data.store).filter(item => this.articleMatches(item, data));
    if (items.length === 0) {
      const details = container.createEl("details", { cls: "litradar-panel litradar-all" });
      details.createEl("summary", { text: "全部条目（0 篇）" });
      details.createDiv({ cls: "litradar-empty-line", text: this.hasFilters() ? "没有符合筛选条件的条目，试试清除筛选。" : this.emptyItemsMessage(data) });
      return;
    }

    const journalItems = items.filter((item) => !isPreprint(item));
    const preprintItems = items.filter((item) => isPreprint(item));
    this.renderItemsSection(container, data, "全部期刊条目", journalItems, "期刊");
    this.renderItemsSection(container, data, "全部预印本", preprintItems, "预印本");
  }

  private renderItemsSection(
    container: HTMLElement,
    data: PanelData,
    title: string,
    items: Article[],
    kind: string,
  ): void {
    const details = container.createEl("details", { cls: "litradar-panel litradar-all" });
    details.addClass(kind === "预印本" ? "litradar-all-preprints" : "litradar-all-journals");
    details.createEl("summary", { text: `${title}（${items.length} 篇）` });
    if (items.length === 0) {
      details.createDiv({ cls: "litradar-empty-line", text: `本日没有${kind}新条目。` });
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
        entry.dataset.stateKey = `article:${item.id}`;
        const summary = entry.createEl("summary");
        summary.createSpan({
          cls: `litradar-item-type${isPreprint(item) ? " is-preprint" : ""}`,
          text: articleTypeLabel(item),
        });
        const url = safeExternalUrl(item.url);
        if (url) {
          const titleLink = summary.createEl("a", {
            cls: "litradar-item-title",
            text: item.title_zh || item.title,
            href: url,
          });
          titleLink.target = "_blank";
          titleLink.rel = "noopener noreferrer";
          titleLink.addEventListener("click", (event) => event.stopPropagation());
        } else {
          summary.createSpan({ cls: "litradar-item-title", text: item.title });
        }
        const subject = articleSubject(item);
        summary.createSpan({
          cls: "litradar-item-meta",
          text:
            ` · ${articleDate(item)}` +
            (item.surface ? ` · ${item.surface}` : "") +
            (subject ? ` · ${subject}` : ""),
        });

        const body = entry.createDiv({ cls: "litradar-item-body" });
        this.renderReadingAction(body, item, data.store?.window?.end || data.activeRunId || undefined);
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
        `计划时间是 ${data.schedule.time}（${data.schedule.tz}）。` +
        "点击上方「立即抓取并生成」，或运行 `python -m litradar run`。",
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
        `${engine.schema ? `（schema ${engine.schema}）` : ""} · 插件 0.3.2`,
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

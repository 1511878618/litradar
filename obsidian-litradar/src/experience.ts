import type { App } from "obsidian";

export interface IdeaDraft {
  brief?: { question: string; notes: string; baseQuestion: string; baseNotes: string };
  members: Record<string, { role: string; note: string; baseRole: string; baseNote: string }>;
  savedAt: string;
}
export interface ContextReceipt {
  status: "attached" | "partial" | "unavailable" | "failed";
  error?: string;
  items: { key: string; title: string; status: string; reason: string }[];
}
export interface DiscussionTask {
  id: string; ideaId: string; name: string; createdAt: string; updatedAt: string;
  paperIds: string[]; state: "running" | "interrupted" | "finished" | "failed";
  phase: string; completed: number; total: number; receipt?: ContextReceipt; error?: string;
}
export interface PanelPreferences {
  panelTab: "today" | "library" | "status"; selectedRunId: string | null;
  query: string; journal: string; articleKind: string; relevantOnly: boolean;
  archiveDays: number; archiveQuery: string; archiveStatus: string; archiveJournal: string;
  archiveKind: string; archiveTopic: string; archiveLimit: number; libraryTab: string;
  scrollTop: number; expanded: string[];
}
export interface ExperienceData {
  drafts: Record<string, IdeaDraft>; recentIdeas: string[]; tasks: DiscussionTask[];
  panel?: PanelPreferences;
  filters: { name: string; state: PanelPreferences }[];
}
const STORAGE_KEY = "litradar-experience-v1";

/** Device-local UI state: never changes the engine's research records. */
export class ExperienceStore {
  data: ExperienceData;
  constructor(private readonly app: App) {
    const saved = app.loadLocalStorage(STORAGE_KEY);
    this.data = { drafts: saved?.drafts || {}, recentIdeas: saved?.recentIdeas || [],
      tasks: saved?.tasks || [], panel: saved?.panel, filters: saved?.filters || [] };
    for (const task of this.data.tasks) if (task.state === "running") {
      task.state = "interrupted";
      task.phase = "上次任务中断，请先在 Zotero 核对会话，再决定是否重新发起";
    }
  }
  save(): void { this.app.saveLocalStorage(STORAGE_KEY, this.data); }
  rememberIdea(id: string): void {
    this.data.recentIdeas = [id, ...this.data.recentIdeas.filter(value => value !== id)].slice(0, 8);
    this.save();
  }
  saveDraft(id: string, draft: IdeaDraft): void {
    if (!draft.brief && !Object.keys(draft.members).length) delete this.data.drafts[id];
    else this.data.drafts[id] = draft;
    this.save();
  }
}

export function friendlyError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  if (/尚不受此上下文适配器支持/.test(raw)) return "llm-for-zotero 版本尚未适配，请在连接状态中查看版本并更新桥接。";
  if (/请安装并启用.*Reading List|reading_not_ready/.test(raw)) return "Reading List 尚未就绪。若已启用，请重启 Zotero 后重新检测。";
  if (/无法连接 Zotero|ECONNREFUSED/.test(raw)) return "连接不到 Zotero，请打开 Zotero 后重新检测。";
  // Keep unknown errors intact, including uncertainty about a submitted write.
  return raw.replace(/^Error:\s*/, "");
}

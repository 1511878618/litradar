import type { Article } from "./types";

export type ReadingStatus = "todo" | "read";
export interface ReadingEntry {
  status: ReadingStatus;
  article: Article;
  updatedAt: string;
}
export type ReadingLibrary = Record<string, ReadingEntry>;
export interface ArticleFilters {
  query: string;
  journal: string;
  kind: string;
  relevantOnly: boolean;
}
export function articleKey(article: Article): string {
  const doi = (article.doi ?? "").trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "").replace(/^doi:\s*/i, "").toLowerCase();
  return doi ? `doi:${doi}` : article.id;
}
export function normalizeLibrary(value: unknown): ReadingLibrary {
  const result: ReadingLibrary = Object.create(null);
  if (!value || typeof value !== "object" || Array.isArray(value)) return result;
  for (const entry of Object.values(value)) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as ReadingEntry;
    if ((row.status !== "todo" && row.status !== "read") || !row.article ||
        typeof row.article.id !== "string" || typeof row.article.title !== "string") continue;
    result[articleKey(row.article)] = { ...row, updatedAt: typeof row.updatedAt === "string" ? row.updatedAt : "" };
  }
  return result;
}
export function matchesArticle(article: Article, filters: ArticleFilters, preprint: boolean, relevant: boolean): boolean {
  const terms = filters.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const text = [article.title, article.title_zh, article.summary_zh, article.why_zh, article.abstract_raw, article.journal, article.doi, JSON.stringify(article.extra?.tags_auto || []), ...(article.authors ?? [])].join(" ").toLowerCase();
  return terms.every(term => text.includes(term)) &&
    (!filters.journal || article.journal === filters.journal) &&
    (!filters.kind || (filters.kind === "preprint") === preprint) &&
    (!filters.relevantOnly || relevant);
}

export interface ZoteroReadingEntry {
  key: string;
  status: string;
  article: Article;
  updatedAt: string;
  selectUrl: string;
  pdfs: Array<{ key: string; url: string }>;
}
export interface ZoteroCollection {
  key: string;
  name: string;
  path?: string;
  parent?: string;
  depth?: number;
}

export interface ReadingListStatusOption { value: string; label: string; }

export function findReadingEntry(entries: ZoteroReadingEntry[], article: Article): ZoteroReadingEntry | undefined {
  const exact = entries.filter(row => articleKey(row.article) === articleKey(article));
  if (exact.length === 1) return exact[0];
  if (exact.length > 1 || article.doi) return undefined;
  // Zotero may normalize creator names, changing the engine ID of a DOI-less item.
  // Match by exact normalized title only when there is a unique candidate.
  const title = (value: string) => value.trim().replace(/\s+/g, " ").toLowerCase();
  const matches = entries.filter(row => !row.article.doi && title(row.article.title) === title(article.title));
  return matches.length === 1 ? matches[0] : undefined;
}

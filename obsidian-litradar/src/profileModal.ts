/**
 * Zotero → research profile modal.
 *
 * The engine reads the local Zotero database (API, with a sqlite fallback), asks
 * the configured LLM to summarize the selected collections, writes
 * `.litradar/profile.yaml`, and the next run uses that direction + keyword list.
 */

import { App, Modal, Notice } from "obsidian";
import type { ZoteroCollection } from "./reading";
import type LitradarPlugin from "./main";

export class ProfileBuilderModal extends Modal {
  private selected = new Set<string>();
  private search = "";
  private listEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;
  private generateButton: HTMLButtonElement | null = null;
  private nameInput: HTMLInputElement | null = null;
  private collectionList: ZoteroCollection[] = [];

  constructor(
    app: App,
    private readonly plugin: LitradarPlugin,
    private readonly onDone: () => void,
  ) {
    super(app);
    this.selected = new Set(plugin.settings.profileCollections);
  }

  async onOpen(): Promise<void> {
    this.titleEl.setText("从 Zotero 生成科研关键词");
    this.contentEl.createEl("p", {
      cls: "litradar-modal-desc",
      text:
        "勾选代表你研究方向的收藏夹；选「我的文章」则从已发表论文提取。" +
        "不选则使用整个文库。生成后会写入 .litradar/profile.yaml，并成为下一轮抓取的方向与关键词。",
    });

    const search = this.contentEl.createEl("input", {
      type: "search",
      placeholder: "筛选收藏夹…",
      cls: "litradar-collection-filter",
    });
    search.addEventListener("input", () => {
      this.search = search.value.trim().toLowerCase();
      this.renderList();
    });

    this.listEl = this.contentEl.createDiv({ cls: "litradar-collection-list litradar-modal-list" });
    this.listEl.createDiv({ cls: "litradar-empty-line", text: "正在读取 Zotero 收藏夹…" });

    const nameRow = this.contentEl.createDiv({ cls: "litradar-modal-name" });
    nameRow.createEl("label", { text: "画像名称" });
    this.nameInput = nameRow.createEl("input", {
      type: "text",
      placeholder: "留空则用收藏夹名 / default",
      value: this.plugin.settings.profileName,
    });

    this.statusEl = this.contentEl.createEl("p", { cls: "litradar-profile-status" });

    const actions = this.contentEl.createDiv({ cls: "litradar-modal-actions" });
    const cancel = actions.createEl("button", { text: "取消" });
    cancel.onclick = () => this.close();
    this.generateButton = actions.createEl("button", {
      text: "生成并应用",
      cls: "mod-cta",
    });
    this.generateButton.disabled = true;
    this.generateButton.onclick = () => void this.generate();

    const loaded = await this.plugin.engineCollections();
    if (!loaded.collections) {
      this.listEl.empty();
      this.listEl.createDiv({
        cls: "litradar-empty-line litradar-error-text",
        text: "读不到 Zotero 收藏夹：" + (loaded.error || "未知原因"),
      });
      if (this.statusEl) {
        this.statusEl.setText(
          "请确认 Zotero 7 已打开，或在 Zotero 设置中允许其他应用通信；" +
            "也可以直接使用整个文库（不勾选）。",
        );
      }
      if (this.generateButton) this.generateButton.disabled = false;
      return;
    }

    this.collectionList = loaded.collections;
    const existing = new Set(this.collectionList.map((item) => item.name));
    this.selected = new Set(Array.from(this.selected).filter((name) => existing.has(name)));
    if (this.generateButton) this.generateButton.disabled = false;
    this.renderList();
  }

  private collections(): ZoteroCollection[] {
    return this.collectionList;
  }

  private renderList(): void {
    if (!this.listEl) return;
    this.listEl.empty();
    const collections = this.collections();
    if (collections.length === 0) {
      this.listEl.createDiv({ cls: "litradar-empty-line", text: "没有收藏夹；将使用整个文库。" });
      return;
    }
    const visible = collections.filter((item) =>
      (item.path || item.name || "").toLowerCase().includes(this.search),
    );
    if (visible.length === 0) {
      this.listEl.createDiv({ cls: "litradar-empty-line", text: "没有匹配的收藏夹。" });
      return;
    }
    for (const item of visible) {
      const label = this.listEl.createEl("label", { cls: "litradar-collection-item" });
      const box = label.createEl("input", { type: "checkbox" });
      box.checked = this.selected.has(item.key) || (this.selected.has(item.name) && this.collectionList.filter(row => row.name === item.name).length === 1);
      box.addEventListener("change", () => {
        this.selected.delete(item.name);
        if (box.checked) this.selected.add(item.key);
        else this.selected.delete(item.key);
        void this.plugin.updateSettings({
          profileCollections: Array.from(this.selected),
        });
      });
      label.createSpan({ text: item.path || item.name });
    }
  }

  private async generate(): Promise<void> {
    if (!this.generateButton) return;
    const names = Array.from(this.selected);
    const name = this.nameInput?.value.trim() ?? "";
    this.generateButton.disabled = true;
    this.generateButton.setText("生成中…");
    if (this.statusEl) {
      this.statusEl.setText(
        names.length > 0
          ? `正在读取 ${names.length} 个收藏夹并调用模型…`
          : "正在读取整个文库并调用模型…",
      );
    }
    const result = await this.plugin.engineProfile(names, name);
    this.generateButton.disabled = false;
    this.generateButton.setText("生成并应用");
    if (!result.ok) {
      new Notice("画像生成失败，请查看面板状态");
      if (this.statusEl) this.statusEl.setText("生成失败：" + (result.error || "未知原因"));
      return;
    }
    new Notice("科研关键词已生成并生效");
    this.close();
    this.onDone();
  }
}

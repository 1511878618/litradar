import { Modal } from "obsidian";
import type LitradarPlugin from "./main";
import { friendlyError } from "./experience";

export interface ConnectionCheck {
  id: "zotero" | "bridge" | "reading" | "llm";
  name: string; state: "ok" | "error" | "unknown";
  message: string; action: string; version: string; detail: string;
}
export interface Diagnosis { checkedAt: string; checks: ConnectionCheck[] }

export class ConnectionModal extends Modal {
  private closed = false;
  constructor(private readonly plugin: LitradarPlugin) { super(plugin.app); }
  async onOpen(): Promise<void> {
    this.titleEl.setText("Zotero 连接状态");
    this.modalEl.addClass("litradar-connections-modal");
    await this.check();
  }
  onClose(): void { this.closed = true; }
  private async check(): Promise<void> {
    this.contentEl.empty();
    this.contentEl.createEl("p", { text: "正在检查本地连接与插件状态…", attr: { role: "status" } });
    try { await this.plugin.checkConnections(); }
    catch (error) {
      if (!this.closed) this.contentEl.createEl("p", { text: friendlyError(error), cls: "litradar-error-text" });
    }
    if (this.closed) return;
    const diagnosis = this.plugin.connections;
    if (diagnosis) {
      this.contentEl.empty();
      this.contentEl.createEl("p", { text: `检测于 ${new Date(diagnosis.checkedAt).toLocaleString()} · 各功能独立检测`, cls: "litradar-muted" });
      for (const check of diagnosis.checks) {
        const row = this.contentEl.createDiv({ cls: `litradar-connection is-${check.state}` });
        row.createEl("strong", { text: `${check.state === "ok" ? "✓" : check.state === "error" ? "!" : "·"} ${check.name}${check.version ? " · " + check.version : ""}` });
        row.createEl("p", { text: check.message });
        if (check.action) row.createEl("p", { text: check.action, cls: "litradar-muted" });
        if (check.detail) {
          const details = row.createEl("details");
          details.createEl("summary", { text: "技术详情" });
          details.createEl("pre", { text: check.detail });
        }
      }
    }
    const retry = this.contentEl.createEl("button", { text: "重新检测", cls: "mod-cta" });
    retry.onclick = () => void this.check();
  }
}

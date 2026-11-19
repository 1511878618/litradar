import { Modal } from "obsidian";
import type LitradarPlugin from "./main";
import type { Article } from "./types";
import type { Idea } from "./ideas";
import { friendlyError } from "./experience";

/** Choose a recent topic once; the engine returns exactly which memberships are new. */
export class QuickIdeaModal extends Modal {
  private closed = false;
  constructor(private readonly plugin: LitradarPlugin, private readonly articles: Article[]) { super(plugin.app); }
  onClose(): void { this.closed = true; }
  async onOpen(): Promise<void> {
    this.titleEl.setText(`加入专题 · ${this.articles.length} 篇`);
    this.modalEl.addClass("litradar-quick-ideas");
    const status = this.contentEl.createEl("p", { text: "正在读取专题…", attr: { role: "status" } });
    try {
      const { ideas } = await this.plugin.ideaCommand<{ ideas: Idea[] }>(["list"]);
      if (this.closed) return;
      status.setText("选择专题即可加入，已有文献会复用。");
      const recent = this.plugin.experience.data.recentIdeas;
      ideas.sort((a, b) => (recent.indexOf(a.id) < 0 ? 99 : recent.indexOf(a.id)) - (recent.indexOf(b.id) < 0 ? 99 : recent.indexOf(b.id)));
      const search = this.contentEl.createEl("input", { type: "search", placeholder: "搜索专题…", attr: { "aria-label": "搜索专题" } });
      const list = this.contentEl.createDiv({ cls: "litradar-quick-idea-list" });
      let busy = false;
      const add = async (idea: Idea) => {
        if (busy) return;
        busy = true;
        search.disabled = true;
        list.querySelectorAll("button").forEach(button => button.disabled = true);
        status.setText("正在加入专题…");
        try {
          const result = await this.plugin.ideaCommand<{ added: Record<string, unknown> }>(["add", "--idea-id", idea.id, "--article-json", JSON.stringify(this.articles)]);
          this.plugin.experience.rememberIdea(idea.id);
          if (this.closed) return;
          this.contentEl.empty();
          const added = Object.keys(result.added).length;
          this.contentEl.createEl("p", { text: `已加入「${idea.name}」：新增 ${added} 篇，复用 ${this.articles.length - added} 篇。`, attr: { role: "status" } });
          if (added) {
            const undo = this.contentEl.createEl("button", { text: "撤销本次加入" });
            undo.onclick = async () => {
              undo.disabled = true;
              try {
                const receipt = await this.plugin.ideaCommand<{ removed: number; preserved: number }>(["undo-add", "--idea-id", idea.id, "--membership-json", JSON.stringify(result.added)]);
                undo.setText(`已撤销 ${receipt.removed} 篇${receipt.preserved ? `；${receipt.preserved} 篇已有后续修改，已保留` : ""}`);
              } catch (error) { undo.disabled = false; this.contentEl.createEl("p", { text: friendlyError(error) }); }
            };
          }
          const open = this.contentEl.createEl("button", { text: "打开专题", cls: "mod-cta" });
          open.onclick = () => { this.close(); this.plugin.openIdeas(); };
        } catch (error) {
          if (this.closed) return;
          status.setText(friendlyError(error)); busy = false; search.disabled = false; render();
        }
      };
      const render = () => {
        list.empty();
        const matches = ideas.filter(idea => idea.name.toLowerCase().includes(search.value.trim().toLowerCase()));
        for (const idea of matches) {
          const button = list.createEl("button", { text: `${idea.name} · ${Object.keys(idea.members).length} 篇${recent.includes(idea.id) ? " · 最近使用" : ""}` });
          button.onclick = () => void add(idea);
        }
        if (!matches.length) list.createEl("p", { text: ideas.length ? "没有匹配的专题" : "还没有专题，在下方输入名称即可新建。" });
      };
      search.oninput = render;
      render();
      const name = this.contentEl.createEl("input", { type:"text", placeholder: "新专题名称", attr: { maxlength: "120", "aria-label": "新专题名称" } });
      const create = this.contentEl.createEl("button", { text: "新建并加入" });
      create.onclick = async () => {
        if (busy || !name.value.trim()) return;
        busy = true;
        create.disabled = true;
        name.disabled = true;
        search.disabled = true;
        list.querySelectorAll("button").forEach(button => button.disabled = true);
        try {
          const idea = await this.plugin.ideaCommand<Idea>(["create", "--name", name.value.trim()]);
          busy = false;
          ideas.unshift(idea); await add(idea);
        } catch (error) { status.setText(friendlyError(error)); busy = false; search.disabled = false; render(); }
        finally { create.disabled = false; name.disabled = false; }
      };
    } catch (error) { if (!this.closed) status.setText(friendlyError(error)); }
  }
}

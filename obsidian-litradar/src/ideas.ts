import { Modal, Notice } from "obsidian";
import type LitradarPlugin from "./main";
import type { Article } from "./types";
import { friendlyError, type IdeaDraft } from "./experience";

export interface Idea {
  id: string; name: string; question: string; notes: string;
  members: Record<string, {role: string; note: string}>;
  analyses: {id: string; created_at: string; report_path: string; count: number}[];
}
export interface IdeaPaper {
  id: string; title: string; title_zh?: string; journal: string; doi?: string;
  abstract_raw: string; zotero_key?: string;
}
interface Handoff {
  id: string; created_at: string; status: "prepared" | "ready" | "received";
  papers: {id:string; title:string}[]; idea: {question: string};
  collection_url?: string; request_path: string; result_path: string;
  zotero_items: Record<string,string>;
  results: {created_at:string; report_path:string; count:number}[];
}
interface Detail {idea: Idea; papers: IdeaPaper[]; tasks?: Handoff[]}
const ROLES = ["待判断", "背景", "方法", "支持证据", "不同结论"];

/** User edits are explicit saves; background radar refreshes cannot reset drafts. */
export class IdeasModal extends Modal {
  private topic: Detail | null = null;
  private topics: Idea[] = [];
  private selected = new Set<string>();
  private busy = false;
  private closed = false;
  private dirty = false;
  private memberDrafts = new Set<string>();
  private get hasDrafts(): boolean { return this.dirty || this.memberDrafts.size > 0; }
  private status!: HTMLElement;
  private body!: HTMLElement;
  private task = "";
  private draftStatus!: HTMLElement;
  private taskArea: HTMLElement | null = null;
  private unsubscribeTasks?: () => void;
  private fulltext = new Map<string, {state: string; reason: string}>();

  constructor(private readonly plugin: LitradarPlugin, private readonly incoming?: Article) { super(plugin.app); }

  async onOpen(): Promise<void> {
    this.titleEl.setText(this.incoming ? "加入研究专题" : "研究专题 · 文献与 idea");
    this.modalEl.addClass("litradar-ideas-modal");
    this.contentEl.createDiv({cls:"litradar-eyebrow",text:"LITRADAR / 研究工作台"});
    this.contentEl.createEl("p", {cls:"litradar-modal-desc", text:this.incoming
      ? `选择专题，将「${this.incoming.title_zh || this.incoming.title}」加入文献库。`
      : "围绕研究问题整理文献，交给 llm-for-zotero 阅读与归纳，通过 Zotero 将 Markdown 保存到 vault。"});
    this.status = this.contentEl.createDiv({attr:{role:"status", "aria-live":"polite"}});
    this.draftStatus = this.contentEl.createDiv({cls:"litradar-draft-status", attr:{role:"status", "aria-live":"polite"}});
    this.body = this.contentEl.createDiv();
    this.unsubscribeTasks = this.plugin.subscribeDiscussions(() => this.renderTasks());
    await this.perform(async()=>{
      await this.loadTopics();
      const recent = this.plugin.experience.data.recentIdeas.find(id => this.topics.some(topic => topic.id === id));
      if (recent) await this.loadTopic(recent);
      this.render();
    });
  }
  onClose(): void { this.closed = true; this.unsubscribeTasks?.(); }

  private async perform(work: ()=>Promise<void>): Promise<void> {
    if(this.busy) return;
    this.busy = true;
    this.status.setText("处理中…");
    this.body.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("button,input,select,textarea").forEach(el=>el.disabled=true);
    try {await work(); if(!this.closed) this.status.setText(this.task || "已完成");}
    catch(error) { if(!this.closed) this.status.setText(friendlyError(error)); }
    finally {
      this.busy=false; this.task="";
      if(!this.closed) this.body.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("button,input,select,textarea").forEach(el=>el.disabled=false);
    }
  }
  private async loadTopics(): Promise<void> {
    this.topics = (await this.plugin.ideaCommand<{ideas:Idea[]}>(["list"])).ideas;
  }
  private async loadTopic(id:string): Promise<void> {
    this.topic = await this.plugin.ideaCommand<Detail>(["get","--idea-id",id]);
    this.selected = new Set(this.topic.papers.length <= 30 ? this.topic.papers.map(p=>p.id) : []);
    this.dirty=false;this.memberDrafts.clear();
    this.fulltext.clear();
    this.plugin.experience.rememberIdea(id);
    const draft = this.plugin.experience.data.drafts[id];
    this.dirty = !!draft?.brief;
    for (const key of Object.keys(draft?.members || {})) if (this.topic.idea.members[key]) this.memberDrafts.add(key);
    this.draftStatus.setText(this.hasDrafts ? "已恢复本机草稿，保存后写入专题" : "");
  }
  private draft(): IdeaDraft {
    return this.plugin.experience.data.drafts[this.topic!.idea.id] || { members: {}, savedAt: "" };
  }
  private persistDraft(draft: IdeaDraft): void {
    draft.savedAt = new Date().toISOString();
    try {
      this.plugin.experience.saveDraft(this.topic!.idea.id, draft);
      this.draftStatus.setText(this.hasDrafts ? "草稿已暂存到本机 · 关闭后可恢复，尚未写入专题" : "已保存到专题");
    } catch (_) { this.draftStatus.setText("草稿暂存失败，请保持窗口打开并点击保存"); }
  }
  private renderTasks(): void {
    if (this.closed || !this.taskArea || !this.topic) return;
    this.taskArea.empty();
    this.taskArea.createEl("h3", {text:"讨论任务记录"});
    const tasks = this.plugin.experience.data.tasks.filter(task => task.ideaId === this.topic!.idea.id);
    if (!tasks.length) this.taskArea.createEl("p", {text:"尚未发起讨论。任务进度与逐篇结果会保留在这里。", cls:"litradar-muted"});
    for (const task of tasks) {
      const row = this.taskArea.createEl("details", {cls:"litradar-discussion-task"});
      row.open = task === tasks[0];
      row.createEl("summary", {text:`${new Date(task.createdAt).toLocaleString()} · ${task.phase}`});
      if (task.state === "running") row.createEl("p", {text:`${task.completed}/${task.total} 篇 · 可关闭弹窗，保持 Obsidian 运行即可继续`});
      if (task.error || task.receipt?.error) row.createEl("p", {text:task.error || task.receipt!.error!, cls:"litradar-error-text"});
      for (const item of task.receipt?.items || []) row.createEl("p", {text:`${item.status === "attached" ? "✓" : "!"} ${item.title}：${item.reason || "请在 Zotero 核对"}`});
    }
  }
  private button(parent:HTMLElement, text:string, action:()=>Promise<void>, primary=false): HTMLButtonElement {
    const button=parent.createEl("button",{text,cls:primary?"mod-cta":""});
    button.onclick=()=>void this.perform(action);return button;
  }
  private render(): void {
    if(this.closed) return;
    this.taskArea=null;
    this.body.empty();
    const nav=this.body.createDiv({cls:"litradar-idea-toolbar litradar-idea-nav"});
    const select=nav.createEl("select",{attr:{"aria-label":"选择研究专题"}});
    select.createEl("option",{value:"",text:"选择研究专题…"});
    for(const idea of this.topics) select.createEl("option",{value:idea.id,text:`${idea.name}（${Object.keys(idea.members).length} 篇）`});
    select.value=this.topic?.idea.id || "";
    select.onchange=()=>{
      void this.perform(async()=>{if(select.value) await this.loadTopic(select.value);else this.topic=null;this.render();});
    };
    const name=nav.createEl("input",{type:"text",placeholder:"新专题名称",attr:{"aria-label":"新专题名称",maxlength:"120"}});
    this.button(nav,"新建专题",async()=>{
      if(!name.value.trim()) throw new Error("请输入新专题名称。");
      const idea=await this.plugin.ideaCommand<Idea>(["create","--name",name.value.trim()]);
      await this.loadTopics();await this.loadTopic(idea.id);this.render();
    });
    if(!this.topic){
      const empty=this.body.createDiv({cls:"litradar-idea-empty"});
      empty.createEl("h3",{text:"从一个研究问题开始"});
      empty.createEl("p",{text:"选择或新建专题，把相关文献、研究问题和自己的判断放在一起。"});
      return;
    }
    const {idea,papers}=this.topic;
    const draft = this.draft();
    if(this.incoming) {
      this.button(this.body,"将这篇文献加入专题",async()=>{
        await this.plugin.ideaCommand(["add","--idea-id",idea.id,"--article-json",JSON.stringify(this.incoming)]);
        new Notice("文献已加入专题");this.close();
      },true);
      return;
    }
    const brief=this.body.createDiv({cls:"litradar-idea-brief"});
    brief.createEl("h3",{text:idea.name});
    const fields=brief.createDiv({cls:"litradar-idea-fields"});
    const questionLabel=fields.createEl("label",{text:"我想弄清什么？",cls:"litradar-idea-field"});
    const question=questionLabel.createEl("textarea",{text:draft.brief?.question ?? idea.question,attr:{rows:"3",placeholder:"例如：这个方向有哪些研究路线，哪些方法适合我的数据？",maxlength:"12000"}});
    const notesLabel=fields.createEl("label",{text:"我的想法",cls:"litradar-idea-field"});
    const notes=notesLabel.createEl("textarea",{text:draft.brief?.notes ?? idea.notes,attr:{rows:"3",placeholder:"记录自己的判断、疑问和计划；这些内容保留在专题中。",maxlength:"12000"}});
    if (draft.brief && (draft.brief.baseQuestion !== idea.question || draft.brief.baseNotes !== idea.notes)) {
      const conflict = brief.createEl("details", {cls:"litradar-draft-conflict"});
      conflict.createEl("summary", {text:"专题内容已有更新，请核对草稿后再保存"});
      conflict.createEl("p", {text:`当前研究问题：${idea.question}`});
      conflict.createEl("p", {text:`当前想法：${idea.notes}`});
    }
    question.oninput=notes.oninput=()=>{
      this.dirty=true;
      const next=this.draft();
      next.brief={question:question.value,notes:notes.value,baseQuestion:next.brief?.baseQuestion ?? idea.question,baseNotes:next.brief?.baseNotes ?? idea.notes};
      this.persistDraft(next);
    };
    this.button(brief,"保存问题与想法",async()=>{
      await this.plugin.ideaCommand(["update","--idea-id",idea.id,"--patch-json",JSON.stringify({question:question.value,notes:notes.value})]);
      idea.question=question.value;idea.notes=notes.value;this.dirty=false;this.task="已保存";
      const next=this.draft();delete next.brief;this.persistDraft(next);
    });
    const importBox=this.body.createEl("details",{cls:"litradar-idea-import"});
    importBox.createEl("summary",{text:"从 Zotero 收藏夹选择文献"});
    const importArea=importBox.createDiv();
    this.button(importArea,"加载收藏夹",async()=>{
      const result=await this.plugin.engineCollections();
      if(!result.collections) throw new Error(result.error || "无法读取 Zotero");
      importArea.empty();
      const collection=importArea.createEl("select",{attr:{"aria-label":"Zotero 收藏夹"}});
      for(const c of result.collections) collection.createEl("option",{value:c.key,text:c.path || c.name});
      const candidates=importArea.createDiv();
      this.button(importArea,"读取文献",async()=>{
        if(!collection.value) throw new Error("请先选择收藏夹");
        const collectionKey=collection.value;
        const result=await this.plugin.ideaCommand<{papers:IdeaPaper[];possibly_truncated:boolean}>(["candidates","--collection",collectionKey]);
        candidates.empty();
        candidates.createEl("p",{text:result.possibly_truncated?"当前显示收藏夹前 100 项中的文献；更早内容可放入较小收藏夹后导入。":`可选 ${result.papers.length} 篇`});
        const chosen=new Set<string>();
        const list=candidates.createDiv({cls:"litradar-idea-candidates"});
        for(const paper of result.papers){
          const label=list.createEl("label");const box=label.createEl("input",{type:"checkbox"});
          label.createSpan({text:`${paper.title_zh || paper.title}${paper.abstract_raw ? "" : " · 无摘要"}`});
          box.onchange=()=>{if(box.checked)chosen.add(paper.id);else chosen.delete(paper.id);};
        }
        this.button(candidates,"导入所选文献",async()=>{
          if(this.hasDrafts) throw new Error("请先保存问题与想法。");
          if(!chosen.size) throw new Error("请选择要导入的文献。");
          await this.plugin.ideaCommand(["import","--idea-id",idea.id,"--collection",collectionKey,"--paper-ids-json",JSON.stringify([...chosen])],60000);
          await this.loadTopics();await this.loadTopic(idea.id);this.render();this.task=`已导入 ${chosen.size} 篇（重复文献会复用）`;
        });
      });
    });
    const header=this.body.createDiv({cls:"litradar-idea-toolbar"});
    header.createEl("h3",{text:`专题文献 · ${papers.length} 篇`});
    const count=header.createEl("span",{cls:"litradar-pill",text:`已选 ${this.selected.size} 篇`});
    const boxes:HTMLInputElement[]=[];
    const selectAll=header.createEl("button",{text:"全选 / 清空"});
    selectAll.onclick=()=>{
      if(papers.length>30 && !this.selected.size){this.status.setText("单次最多附加 30 篇，请手动选择。");return;}
      this.selected=this.selected.size?new Set():new Set(papers.map(p=>p.id));
      boxes.forEach(box=>box.checked=this.selected.has(box.value));count.setText(`已选 ${this.selected.size} 篇`);
    };
    const list=this.body.createDiv({cls:"litradar-idea-papers"});
    for(const paper of papers){
      const row=list.createEl("details",{cls:"litradar-idea-paper"});
      const summary=row.createEl("summary");
      const box=summary.createEl("input",{type:"checkbox",value:paper.id,attr:{"aria-label":`选择 ${paper.title}`}});boxes.push(box);
      box.checked=this.selected.has(paper.id);box.onclick=event=>event.stopPropagation();
      box.onchange=()=>{if(box.checked)this.selected.add(paper.id);else this.selected.delete(paper.id);count.setText(`已选 ${this.selected.size} 篇`);};
      summary.createSpan({text:paper.title_zh || paper.title});
      const fulltext=this.fulltext.get(paper.id);
      summary.createEl("small",{text:fulltext?.reason || "全文尚未检查",cls:"litradar-fulltext-status"});
      row.createEl("p",{text:`${paper.journal || "未知期刊"}${paper.doi ? " · DOI: "+paper.doi : ""}`});
      row.createEl("p",{text:paper.abstract_raw || "未提供摘要"});
      const role=row.createEl("select",{attr:{"aria-label":"文献在专题中的角色"}});
      for(const value of ROLES)role.createEl("option",{value,text:value});role.value=draft.members[paper.id]?.role ?? idea.members[paper.id].role;
      const note=row.createEl("textarea",{text:draft.members[paper.id]?.note ?? idea.members[paper.id].note,attr:{rows:"2",placeholder:"这篇文章与我的想法有什么关系？",maxlength:"6000"}});
      const memberDraft=draft.members[paper.id];
      if(memberDraft && (memberDraft.baseRole!==idea.members[paper.id].role || memberDraft.baseNote!==idea.members[paper.id].note)) row.createEl("p",{text:`专题批注已有更新：${idea.members[paper.id].role} · ${idea.members[paper.id].note}。请核对后保存草稿。`,cls:"litradar-draft-conflict"});
      role.onchange=note.oninput=()=>{
        this.memberDrafts.add(paper.id);
        const next=this.draft();const old=next.members[paper.id];
        next.members[paper.id]={role:role.value,note:note.value,baseRole:old?.baseRole ?? idea.members[paper.id].role,baseNote:old?.baseNote ?? idea.members[paper.id].note};
        this.persistDraft(next);
      };
      const paperActions=row.createDiv({cls:"litradar-idea-paper-actions"});
      const save=this.button(paperActions,"保存角色与批注",async()=>{
        await this.plugin.ideaCommand(["member","--idea-id",idea.id,"--paper-id",paper.id,"--role",role.value,"--note",note.value]);
        idea.members[paper.id]={role:role.value,note:note.value};this.memberDrafts.delete(paper.id);save.setText("已保存角色与批注");
        const next=this.draft();delete next.members[paper.id];this.persistDraft(next);
      });
      this.button(paperActions,"从专题移出",async()=>{
        if(this.hasDrafts) throw new Error("请先保存问题与想法。");
        await this.plugin.ideaCommand(["member","--idea-id",idea.id,"--paper-id",paper.id,"--remove"]);
        await this.loadTopics();await this.loadTopic(idea.id);this.render();
      });
    }
    const workflow=this.body.createDiv({cls:"litradar-idea-collaboration"});
    workflow.createEl("h3",{text:"在 Zotero 继续研究"});
    workflow.createEl("p",{text:"选择 1–30 篇文献，加入 llm-for-zotero 的新对话上下文，然后在 Zotero 自由提问。已有条目直接复用；未入库文献会先加入 Zotero，没有本地全文附件的条目会提示补充。"});
    workflow.createEl("p",{cls:"litradar-muted",text:"聊完后，使用 llm-for-zotero 自己的 Markdown 保存功能。首次请将它的输出目录手动设为当前 vault 下的文件夹（例如 02-Reading/研究专题）。研究问题与批注保留在这里，不会自动发送。"});
    const connection=workflow.createEl("button", {text:"连接状态"});
    connection.onclick=()=>this.plugin.openConnections();
    this.button(workflow,"检查所选全文",async()=>{
      if(!this.selected.size || this.selected.size>30) throw new Error("请选择 1–30 篇文献。");
      const result=await this.plugin.ideaCommand<{items:{id:string;state:string;reason:string}[]}>(["check","--idea-id",idea.id,"--paper-ids-json",JSON.stringify([...this.selected])],120000);
      result.items.forEach(item=>this.fulltext.set(item.id,item));
      this.render();this.task=`已检查 ${result.items.length} 篇全文，${result.items.filter(item=>item.state==="ready").length} 篇可用`;
    });
    this.button(workflow,"在 Zotero 中讨论所选文献",async()=>{
      if(this.hasDrafts) throw new Error("请先保存问题与想法。");
      if(!this.selected.size || this.selected.size>30) throw new Error("请选择 1–30 篇文献。");
      this.status.setText("任务已开始，可关闭弹窗；进度与结果保存在下方任务记录。");
      const task=await this.plugin.startDiscussion(idea.id,idea.name,[...this.selected]);
      this.task=task.error || task.phase;
    },true);
    this.taskArea=this.body.createDiv({cls:"litradar-discussion-history",attr:{"aria-live":"polite"}});
    this.renderTasks();
    const versions=[...idea.analyses, ...(this.topic.tasks || []).flatMap(task=>task.results)];
    if(versions.length) {
      const history=this.body.createEl("details");history.createEl("summary",{text:"旧版归纳记录"});
      for(const version of [...versions].reverse()) {
        this.button(history,`${version.created_at.replace("T"," ")} · ${version.count} 篇 · 打开归纳`,async()=>{await this.plugin.openIdeaNote(version.report_path);this.close();});
      }
    }
  }
}

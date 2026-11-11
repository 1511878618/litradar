import { Modal, Notice } from "obsidian";
import type LitradarPlugin from "./main";
import type { Article } from "./types";

export interface Idea {
  id: string; name: string; question: string; notes: string;
  members: Record<string, {role: string; note: string}>;
  analyses: {id: string; created_at: string; report_path: string; count: number}[];
}
export interface IdeaPaper {
  id: string; title: string; title_zh?: string; journal: string; doi?: string;
  abstract_raw: string; zotero_key?: string;
}
interface Detail {idea: Idea; papers: IdeaPaper[]}
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

  constructor(private readonly plugin: LitradarPlugin, private readonly incoming?: Article) { super(plugin.app); }

  async onOpen(): Promise<void> {
    this.titleEl.setText(this.incoming ? "加入研究专题" : "研究专题 · 文献与 idea");
    this.modalEl.addClass("litradar-ideas-modal");
    this.contentEl.createEl("p", {cls:"litradar-muted", text:this.incoming
      ? `选择专题，将「${this.incoming.title_zh || this.incoming.title}」加入文献库。`
      : "围绕研究问题整理文献，选择 1–50 篇生成带来源的背景、研究主线和对照表。"});
    this.status = this.contentEl.createDiv({attr:{role:"status", "aria-live":"polite"}});
    this.body = this.contentEl.createDiv();
    await this.perform(async()=>{await this.loadTopics(); this.render();});
  }
  onClose(): void { this.closed = true; }

  private async perform(work: ()=>Promise<void>): Promise<void> {
    if(this.busy) return;
    this.busy = true;
    this.status.setText("处理中…");
    this.body.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("button,input,select,textarea").forEach(el=>el.disabled=true);
    try {await work(); if(!this.closed) this.status.setText(this.task || "已完成");}
    catch(error) { if(!this.closed) this.status.setText(error instanceof Error ? error.message : String(error)); }
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
    this.selected = new Set(this.topic.papers.length <= 50 ? this.topic.papers.map(p=>p.id) : []);
    this.dirty=false;this.memberDrafts.clear();
  }
  private button(parent:HTMLElement, text:string, action:()=>Promise<void>, primary=false): HTMLButtonElement {
    const button=parent.createEl("button",{text,cls:primary?"mod-cta":""});
    button.onclick=()=>void this.perform(action);return button;
  }
  private render(): void {
    if(this.closed) return;
    this.body.empty();
    const nav=this.body.createDiv({cls:"litradar-idea-toolbar"});
    const select=nav.createEl("select",{attr:{"aria-label":"选择研究专题"}});
    select.createEl("option",{value:"",text:"选择研究专题…"});
    for(const idea of this.topics) select.createEl("option",{value:idea.id,text:`${idea.name}（${Object.keys(idea.members).length} 篇）`});
    select.value=this.topic?.idea.id || "";
    select.onchange=()=>{
      if(this.hasDrafts){select.value=this.topic?.idea.id || "";this.status.setText("请先保存研究问题和个人想法，再切换专题。");return;}
      void this.perform(async()=>{if(select.value) await this.loadTopic(select.value);else this.topic=null;this.render();});
    };
    const name=nav.createEl("input",{type:"text",placeholder:"新专题名称",attr:{"aria-label":"新专题名称",maxlength:"120"}});
    this.button(nav,"新建专题",async()=>{
      if(this.hasDrafts) throw new Error("请先保存当前专题的修改。");
      if(!name.value.trim()) throw new Error("请输入新专题名称。");
      const idea=await this.plugin.ideaCommand<Idea>(["create","--name",name.value.trim()]);
      await this.loadTopics();await this.loadTopic(idea.id);this.render();
    });
    if(!this.topic){this.body.createEl("p",{text:"先选择或新建一个专题。可以从一个宽泛兴趣开始，再逐渐收窄研究问题。"});return;}
    const {idea,papers}=this.topic;
    if(this.incoming) {
      this.button(this.body,"将这篇文献加入专题",async()=>{
        await this.plugin.ideaCommand(["add","--idea-id",idea.id,"--article-json",JSON.stringify(this.incoming)]);
        new Notice("文献已加入专题");this.close();
      },true);
      return;
    }
    const questionLabel=this.body.createEl("label",{text:"我想弄清什么？",cls:"litradar-idea-field"});
    const question=questionLabel.createEl("textarea",{text:idea.question,attr:{rows:"3",placeholder:"例如：这个方向有哪些研究路线，哪些方法适合我的数据？",maxlength:"12000"}});
    const notesLabel=this.body.createEl("label",{text:"我的想法",cls:"litradar-idea-field"});
    const notes=notesLabel.createEl("textarea",{text:idea.notes,attr:{rows:"3",placeholder:"记录自己的判断、疑问和计划；生成分析不会改写这里。",maxlength:"12000"}});
    question.oninput=notes.oninput=()=>{this.dirty=true;};
    this.button(this.body,"保存问题与想法",async()=>{
      await this.plugin.ideaCommand(["update","--idea-id",idea.id,"--patch-json",JSON.stringify({question:question.value,notes:notes.value})]);
      idea.question=question.value;idea.notes=notes.value;this.dirty=false;this.task="已保存";
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
    const count=header.createEl("span",{text:`已选 ${this.selected.size} 篇`});
    const boxes:HTMLInputElement[]=[];
    const selectAll=header.createEl("button",{text:"全选 / 清空"});
    selectAll.onclick=()=>{
      if(papers.length>50 && !this.selected.size){this.status.setText("单次最多分析 50 篇，请手动选择。");return;}
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
      summary.createEl("small",{text:paper.abstract_raw ? "有摘要" : "无摘要 · 本次将跳过归纳"});
      row.createEl("p",{text:`${paper.journal || "未知期刊"}${paper.doi ? " · DOI: "+paper.doi : ""}`});
      row.createEl("p",{text:paper.abstract_raw || "未提供摘要"});
      const role=row.createEl("select",{attr:{"aria-label":"文献在专题中的角色"}});
      for(const value of ROLES)role.createEl("option",{value,text:value});role.value=idea.members[paper.id].role;
      const note=row.createEl("textarea",{text:idea.members[paper.id].note,attr:{rows:"2",placeholder:"这篇文章与我的想法有什么关系？",maxlength:"6000"}});
      role.onchange=note.oninput=()=>{this.memberDrafts.add(paper.id);};
      const save=this.button(row,"保存角色与批注",async()=>{
        await this.plugin.ideaCommand(["member","--idea-id",idea.id,"--paper-id",paper.id,"--role",role.value,"--note",note.value]);
        idea.members[paper.id]={role:role.value,note:note.value};this.memberDrafts.delete(paper.id);save.setText("已保存角色与批注");
      });
      this.button(row,"从专题移出",async()=>{
        if(this.hasDrafts) throw new Error("请先保存问题与想法。");
        await this.plugin.ideaCommand(["member","--idea-id",idea.id,"--paper-id",paper.id,"--remove"]);
        await this.loadTopics();await this.loadTopic(idea.id);this.render();
      });
    }
    this.body.createEl("p",{cls:"litradar-muted",text:"生成时将所选论文的标题、摘要、研究问题及已保存批注发送给配置的模型。第一版采用摘要级分析，未读取 PDF 全文；结果保留原文依据与历史版本。"});
    this.button(this.body,"生成背景、研究主线与对照表",async()=>{
      if(this.hasDrafts) throw new Error("请先保存问题与想法。");
      if(!this.selected.size || this.selected.size>50) throw new Error("请选择 1–50 篇文献。");
      this.status.setText("正在提取文献并交叉归纳…可能需要几分钟。失败后重试会复用已完成提取。");
      const result=await this.plugin.ideaCommand<{report_path:string;coverage:{selected:number;used:number}}>(["analyze","--idea-id",idea.id,"--paper-ids-json",JSON.stringify([...this.selected])],30*60*1000);
      await this.loadTopics();await this.loadTopic(idea.id);this.render();
      this.task=`已生成：选中 ${result.coverage.selected} 篇，实际用于归纳 ${result.coverage.used} 篇`;
      if(!this.closed) await this.plugin.openIdeaNote(result.report_path);
      new Notice(this.task);this.close();
    },true);
    const history=this.body.createDiv();history.createEl("h3",{text:"归纳历史"});
    if(!idea.analyses.length) history.createEl("p",{text:"尚未生成归纳。结果会另存为 Obsidian 笔记，每次生成保留独立版本。"});
    for(const version of [...idea.analyses].reverse()) {
      this.button(history,`${version.created_at.replace("T"," ")} · ${version.count} 篇 · 打开归纳`,async()=>{await this.plugin.openIdeaNote(version.report_path);this.close();});
    }
  }
}

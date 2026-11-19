const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('../node_modules/typescript');
class El {
 constructor(tag='div',props={}) {this.tag=tag;this.children=[];this.value=props.value??props.text??'';this.text=props.text||'';this.disabled=false;this.checked=false;}
 createEl(tag,props={}){const el=new El(tag,props);this.children.push(el);return el;}
 createDiv(props={}){return this.createEl('div',props);}
 createSpan(props={}){return this.createEl('span',props);}
 setText(text){this.text=text;}
 addClass(){}
 empty(){this.children=[];}
 all(){return this.children.flatMap(c=>[c,...c.all()]);}
 querySelectorAll(){return this.all().filter(el=>['button','input','select','textarea'].includes(el.tag));}
}
class Modal {constructor(){this.titleEl=new El();this.modalEl=new El();this.contentEl=new El();}close(){this.onClose();}}
let copied="",external="";
const exportsObject={};
const experience={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/experience.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{exports:experience});
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ideas.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{exports:exportsObject,require:dep=>dep==='./experience'?experience:dep==='electron'?{shell:{openExternal:async url=>{external=url;}}}:{Modal,FuzzySuggestModal:class{},Notice:class{}},navigator:{clipboard:{writeText:async text=>{copied=text;}}}});
const find=(view,text)=>view.contentEl.all().find(e=>e.tag==='button'&&e.text===text);
const flush=async()=>{for(let n=0;n<40;n++)await Promise.resolve();};
(async()=>{
 const idea={id:'idea',name:'My topic',question:'What methods?',notes:'My thinking',members:{paper:{role:'方法',note:''}},analyses:[]};
 const paper={id:'paper',title:'Paper',journal:'Nature',abstract_raw:'Evidence text'};
 let calls=[],fail=false,partial=false,tasks=[];
 const task={id:'task',created_at:'2026-11-12T10:00:00',status:'prepared',papers:[paper],idea:{question:'What methods?'},zotero_items:{},results:[],prompt:'Research question and exact papers',collection_url:'zotero://select/library/collections/COLL0001',request_path:'02-Reading/研究专题/task.md',result_path:'02-Reading/研究专题/result.md'};
 let disk=null;
 const listeners=new Set();
 const app={loadLocalStorage:()=>disk,saveLocalStorage:(key,value)=>{disk=JSON.parse(JSON.stringify(value));}};
 const plugin={app,experience:new experience.ExperienceStore(app),subscribeDiscussions:fn=>{listeners.add(fn);return()=>listeners.delete(fn);},ideaCommand:async args=>{
  calls.push(args);
  if(fail)throw new Error('Service unavailable');
  if(args[0]==='list')return {ideas:[idea]};
  if(args[0]==='get')return {idea,papers:[paper],tasks};
  if(args[0]==='analyze')throw new Error('Direct LLM analysis must never run');
  if(['analyze','handoff','sync','receive'].includes(args[0]))throw new Error('Unexpected legacy workflow');
  if(args[0]==='discuss')return {status:partial?'unavailable':'attached',items:[{key:'ITEM0000',title:'Paper',status:partial?'skipped':'attached',reason:partial?'没有默认附件':'已加入会话上下文'}]};
  return idea;
 },startDiscussion:async(ideaId,name,ids)=>{
   const receipt=await plugin.ideaCommand(['discuss','--paper-ids-json',JSON.stringify(ids)]);
   const task={id:String(plugin.experience.data.tasks.length),ideaId,name,createdAt:new Date().toISOString(),state:'finished',receipt,
     phase:`已确认附加 ${receipt.items.filter(i=>i.status==='attached').length}/${receipt.items.length} 篇`};
   plugin.experience.data.tasks.unshift(task);plugin.experience.save();listeners.forEach(fn=>fn());return task;
 },openIdeaNote:async path=>{throw new Error("No result navigation expected");}};
 const view=new exportsObject.IdeasModal(plugin);
 await view.onOpen();await view.loadTopic('idea');view.render();
 assert.equal(view.selected.size,1);
 const question=view.contentEl.all().find(el=>el.tag==='textarea'&&el.value==='What methods?');
 question.value='New question';question.oninput();
 find(view,'在 Zotero 中讨论所选文献').onclick();await flush();
 assert.equal(calls.filter(c=>c[0]==='discuss').length,0,'Unsaved question must not be analyzed as if saved');
 find(view,'保存问题与想法').onclick();await flush();assert.equal(view.dirty,false);
 const memberNote=view.contentEl.all().find(el=>el.tag==='textarea'&&el.value==='');
 memberNote.value='Unsaved insight';memberNote.oninput();
 find(view,'在 Zotero 中讨论所选文献').onclick();await flush();
 assert.equal(calls.filter(c=>c[0]==='discuss').length,0,'Unsaved paper notes must be saved first');
 assert.equal(view.hasDrafts,true);
 find(view,'保存角色与批注').onclick();await flush();assert.equal(view.hasDrafts,false);
 fail=true;await view.perform(async()=>{await plugin.ideaCommand(['get']);});
 assert.match(view.status.text,/Service unavailable/);assert.equal(view.busy,false);
 assert.equal(question.value,'New question','Failure retains the draft UI');
 fail=false;partial=true;find(view,'在 Zotero 中讨论所选文献').onclick();await flush();
 assert.match(view.status.text,/0\/1/);
 assert.ok(view.contentEl.all().some(el=>el.text.includes('没有默认附件')));
 assert.equal(view.closed,false);
 partial=false;find(view,'在 Zotero 中讨论所选文献').onclick();await flush();
 assert.match(view.status.text,/1\/1/);
 assert.ok(calls.find(c=>c[0]==='discuss'&&c.includes('["paper"]')));
 assert.ok(!calls.some(c=>['analyze','handoff','sync','receive'].includes(c[0])));
 assert.ok(!find(view,'复制分析任务')&&!find(view,'收回指定路径的结果'));
 view.selected=new Set(Array.from({length:31},(_,i)=>String(i)));
 find(view,'在 Zotero 中讨论所选文献').onclick();await flush();assert.match(view.status.text,/1–30/);
 question.value='Draft survives Escape';question.oninput();
 memberNote.value='Unsaved paper insight';memberNote.oninput();
 assert.equal(disk.drafts.idea.brief.question,'Draft survives Escape');
 view.close();assert.equal(listeners.size,0);
 plugin.experience=new experience.ExperienceStore(app);
 const reopened=new exportsObject.IdeasModal(plugin);await reopened.onOpen();
 assert.ok(reopened.contentEl.all().some(el=>el.tag==='textarea'&&el.value==='Draft survives Escape'));
 assert.ok(reopened.contentEl.all().some(el=>el.tag==='textarea'&&el.value==='Unsaved paper insight'));
 assert.ok(reopened.contentEl.all().some(el=>el.text.includes('已确认附加')),'Receipts survive closing the dialog');
 find(reopened,'保存问题与想法').onclick();await flush();
 assert.equal(disk.drafts.idea.brief,undefined);assert.ok(disk.drafts.idea.members.paper,'Saving brief does not clear paper drafts');
 find(reopened,'保存角色与批注').onclick();await flush();assert.equal(disk.drafts.idea,undefined);
 console.log('PASS: durable draft recovery, independent saves, stored receipts, exact selection and no model calls');
})().catch(error=>{console.error(error);process.exitCode=1;});

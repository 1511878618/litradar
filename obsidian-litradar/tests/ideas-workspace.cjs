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
const exportsObject={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/ideas.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{exports:exportsObject,require:()=>({Modal,Notice:class{}})});
const find=(view,text)=>view.contentEl.all().find(e=>e.tag==='button'&&e.text===text);
const flush=async()=>{for(let n=0;n<40;n++)await Promise.resolve();};
(async()=>{
 const idea={id:'idea',name:'My topic',question:'What methods?',notes:'My thinking',members:{paper:{role:'方法',note:''}},analyses:[]};
 const paper={id:'paper',title:'Paper',journal:'Nature',abstract_raw:'Evidence text'};
 let calls=[],fail=false,opened='';
 const plugin={app:{},ideaCommand:async args=>{
  calls.push(args);
  if(fail)throw new Error('Service unavailable');
  if(args[0]==='list')return {ideas:[idea]};
  if(args[0]==='get')return {idea,papers:[paper]};
  if(args[0]==='analyze')return {report_path:'02-Reading/研究专题/result.md',coverage:{selected:1,used:1}};
  return idea;
 },openIdeaNote:async path=>{opened=path;}};
 const view=new exportsObject.IdeasModal(plugin);
 await view.onOpen();await view.loadTopic('idea');view.render();
 assert.equal(view.selected.size,1);
 const question=view.contentEl.all().find(el=>el.tag==='textarea'&&el.value==='What methods?');
 question.value='New question';question.oninput();
 find(view,'生成背景、研究主线与对照表').onclick();await flush();
 assert.equal(calls.filter(c=>c[0]==='analyze').length,0,'Unsaved question must not be analyzed as if saved');
 find(view,'保存问题与想法').onclick();await flush();assert.equal(view.dirty,false);
 const memberNote=view.contentEl.all().find(el=>el.tag==='textarea'&&el.value==='');
 memberNote.value='Unsaved insight';memberNote.oninput();
 find(view,'生成背景、研究主线与对照表').onclick();await flush();
 assert.equal(calls.filter(c=>c[0]==='analyze').length,0,'Unsaved paper notes must be saved first');
 assert.equal(view.hasDrafts,true);
 find(view,'保存角色与批注').onclick();await flush();assert.equal(view.hasDrafts,false);
 fail=true;await view.perform(async()=>{await plugin.ideaCommand(['analyze']);});
 assert.match(view.status.text,/Service unavailable/);assert.equal(view.busy,false);
 assert.equal(question.value,'New question','Failure retains the draft UI');
 fail=false;find(view,'生成背景、研究主线与对照表').onclick();await flush();
 assert.equal(opened,'02-Reading/研究专题/result.md');assert.equal(view.closed,true);
 assert.ok(calls.find(c=>c[0]==='analyze'&&c.includes('["paper"]')));
 console.log('PASS: workspace selection, unsaved question/note guards, error recovery and generated-note navigation');
})().catch(error=>{console.error(error);process.exitCode=1;});

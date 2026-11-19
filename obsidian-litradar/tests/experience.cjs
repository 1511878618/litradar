const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('../node_modules/typescript');
const cache={};let run;
function load(name){
 if(cache[name])return cache[name];
 const exports={};cache[name]=exports;
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(`src/${name}.ts`,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{
  exports,require:dep=>dep==='obsidian'?{Plugin:class{},ItemView:class{},Notice:class{}}:dep==='./bridge'?{Bridge:class{run(...args){return run(...args);}}}:['./experience','./reading','./types'].includes(dep)?load(dep.slice(2)):{},
 });return exports;
}
(async()=>{
 const {ExperienceStore,friendlyError}=load('experience');let disk=null;
 const app={loadLocalStorage:()=>disk,saveLocalStorage:(key,data)=>{disk=JSON.parse(JSON.stringify(data));}};
 let store=new ExperienceStore(app);
 store.rememberIdea('a');store.rememberIdea('b');store.rememberIdea('a');assert.equal(store.data.recentIdeas.join(','),'a,b');
 store.data.tasks=[{id:'old',state:'running',phase:'Waiting'}];store.save();store=new ExperienceStore(app);
 assert.equal(store.data.tasks[0].state,'interrupted');assert.match(store.data.tasks[0].phase,/核对/);
 assert.match(friendlyError('Zotero HTTP 503：llm-for-zotero 3.9.19 尚不受此上下文适配器支持'),/尚未适配/);
 assert.match(friendlyError('Zotero HTTP 503：请安装并启用 Zotero Reading List'),/尚未就绪/);
 assert.match(friendlyError('Write timeout; verify before retry'),/verify/);
 const {fulltextState}=load('reading');
 assert.equal(fulltextState().action,'inspect');
 assert.equal(fulltextState({key:'A',pdfs:[],pdfChecked:true}).action,'download');
 assert.match(fulltextState({key:'A',pdfs:[{available:null}]}).label,/未确认/);
 assert.match(fulltextState({key:'A',pdfChecked:true,pdfs:[{available:false}]}).label,/未下载/);
 assert.equal(fulltextState({key:'A',pdfChecked:true,pdfs:[{available:true}]}).tone,'ready');
 const Plugin=load('main').default,plugin=new Plugin();plugin.experience=store;plugin.refreshViews=()=>{};plugin.settings.vaultRoot='/tmp';
 let release;run=async(args,timeout,onEvent)=>{
   assert.ok(args.includes('discuss'));
   onEvent({event:'context_progress',phase:'附加文献到新会话',completed:1,total:1});
   await new Promise(resolve=>release=resolve);
   onEvent({event:'context_result',receipt:{status:'partial',items:[{key:'K',title:'Paper',status:'skipped',reason:'No PDF'}]}});
   return {code:0};
 };
 const task=plugin.startDiscussion('a','Topic',['paper']);
 assert.equal(disk.tasks[0].state,'running');assert.equal(disk.tasks[0].phase,'附加文献到新会话');
 await assert.rejects(()=>plugin.startDiscussion('b','Other',['paper']),/已有讨论任务/);
 release();await task;
 assert.equal(disk.tasks[0].state,'finished');assert.equal(disk.tasks[0].receipt.items[0].reason,'No PDF');
 assert.equal(new ExperienceStore(app).data.tasks[0].state,'finished');
 run=async()=>({timedOut:true});await plugin.startDiscussion('a','Topic',['paper']);
 assert.equal(disk.tasks[0].state,'interrupted');assert.match(disk.tasks[0].phase,/未自动重发/);
 console.log('PASS: durable task progress/results, interrupted restart, concurrency guard, unknown vs missing PDFs, actionable errors');
})().catch(error=>{console.error(error);process.exitCode=1;});

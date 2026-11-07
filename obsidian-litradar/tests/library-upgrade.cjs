const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('../node_modules/typescript');
const cache={};
function load(name){if(cache[name])return cache[name];const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(`src/${name}.ts`,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2018}}).outputText,{exports,window:{setTimeout:callback=>{callback();return 1;}},require:dep=>dep==='obsidian'?{Plugin:class{},PluginSettingTab:class{},Modal:class{},ItemView:class{},Notice:class{},FileSystemAdapter:class{},TFile:class{}}:dep.startsWith('./')?load(dep.slice(2)):require(dep)});return cache[name]=exports;}
(async()=>{
 const {LitradarStore}=load('store'); const reader=new LitradarStore({},'.litradar');
 const article={id:'doi:10.1/a',doi:'10.1/a',title:'Cardiac prediction',journal:'Nature'};
 reader.readJson=async path=>path.endsWith('presentation.json')?{titles:{[article.id]:'心脏预测'},summaries:{[article.id]:'分析心脏风险'},extras:{[article.id]:{highlight:true}}}:path.endsWith('versions.json')?{'10.1/a':[{doi:'10.1/pre'}]}:{items:[{...article}]};
 const enriched=await reader.readStore('2026-11-04');assert.equal(enriched.items[0].title_zh,'心脏预测');assert.equal(enriched.items[0].title,'Cardiac prediction');
 reader.listRunIds=async()=>['2026-11-03','2026-W45','2026-11-04'];
 const archive=await reader.readArchive(0);assert.equal(archive.length,1);assert.equal(archive[0].extra.report_dates.length,2);assert.equal(archive[0].extra.versions[0].doi,'10.1/pre');
 const {matchesArticle}=load('reading');assert.equal(matchesArticle(enriched.items[0],{query:'心脏 风险',journal:'',kind:'',relevantOnly:false},false,true),true);
 const Plugin=load('main').default,p=new Plugin();p.refreshViews=()=>{};
 p.zoteroCommand=async()=>({key:'ABCD1234',pdfs:[{key:'PDF12345'}]});
 await p.inspectZoteroArticle(article);assert.equal(p.articleStates.get(article.doi).entry.pdfs.length,1);
 p.zoteroCommand=async()=>{throw new Error('Zotero unavailable');};await p.inspectZoteroArticle(article);assert.match(p.articleStates.get(article.doi).error,/unavailable/);
 p.auxiliaryProbe=async()=>({code:1,stderr:'denied'});await assert.rejects(()=>p.saveRecommendationFeedback(article,'less'),/denied/);
 console.log('PASS: Chinese metadata, archive DOI deduplication, version links, multilingual search, PDF lookup and feedback errors');
})().catch(error=>{console.error(error);process.exitCode=1;});

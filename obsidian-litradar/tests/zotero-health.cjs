const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 let available=false;
 class Items{}
 const item={deleted:false,isRegularItem:()=>true,getAttachments:()=>[1],getBestAttachment:async()=>({fileExists:async()=>available})};
 const Zotero={version:'10.0.7',Server:{LocalAPI:{Items},Endpoints:{}},Items:{getByLibraryAndKey:(_,key)=>key==='ITEM0001'?item:null,getAsync:async()=>({key:'FILE0001',attachmentContentType:'application/pdf',fileExists:async()=>available})}};
 const context=vm.createContext({Zotero,downloading:new Set(),ChromeUtils:{importESModule:()=>({AddonManager:{getAddonByID:async id=>({version:id.includes('reading')?'1.7.2':'3.9.19',isActive:true})}})},
   readingOverlay(){throw new Error('Not initialized');},llmContextHost(){throw new Error('llm-for-zotero 3.9.19 尚不受此上下文适配器支持');}});
 vm.runInContext(fs.readFileSync('../zotero-litradar/health.js','utf8'),context);context.registerHealthEndpoints('0.3.1');
 const health=new Zotero.Server.Endpoints['/api/users/:userID/litradar/health']();
 assert.ok(health instanceof Items);assert.equal(health.supportedMethods.join(),'GET');assert.equal(Object.hasOwn(Object.getPrototypeOf(health),'init'),false);
 const status=JSON.parse((await health.run())[2]);assert.equal(status.reading.enabled,true);assert.equal(status.reading.ready,false);assert.equal(status.llm.version,'3.9.19');
 const files=new Zotero.Server.Endpoints['/api/users/:userID/litradar/files']();
 assert.ok(files instanceof Items);assert.equal(files.supportedMethods.join(),'GET');
 const request=key=>({libraryID:1,searchParams:new URLSearchParams({key})});
 assert.equal((await files.run(request('../bad')))[0],400);
 assert.equal((await files.run(request('MISSING1')))[0],404);
 let result=JSON.parse((await files.run(request('ITEM0001')))[2]);assert.equal(result.files[0].available,false);assert.equal(result.contextAvailable,false);
 available=true;result=JSON.parse((await files.run(request('ITEM0001')))[2]);assert.equal(result.files[0].available,true);assert.equal(result.contextAvailable,true);
 assert.ok(!JSON.stringify(result).includes('/Users/'));
 context.unregisterHealthEndpoints();assert.equal(Object.keys(Zotero.Server.Endpoints).length,0);
 console.log('PASS: read-only diagnostics, inherited native authorization, exact local file availability, cleanup');
})().catch(error=>{console.error(error);process.exitCode=1;});

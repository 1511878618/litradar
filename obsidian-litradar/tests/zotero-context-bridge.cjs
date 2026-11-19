const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function fixture({missing=false,old=false,unexpected=false,unavailable=false,initializing=false}={}) {
 let selected=[],commandCount=0,now=0,root=null;
 const rows=[1,2].map(id=>({id,deleted:false,isRegularItem:()=>true,getField:()=>`Paper ${id}`,
   getBestAttachment:async()=>missing&&id===2?null:{getFilePathAsync:async()=>'/file.pdf'}}));
 const panel=(conversation,ids)=>({dataset:{itemId:conversation,libraryId:'1',conversationKind:'global'},querySelectorAll:()=>ids.map(id=>({dataset:{paperItemId:String(id)}}))});
 root=panel('old',[1,2]);
 const menu={dispatchEvent(){commandCount++;root=panel(initializing?'mounting':old?'old':'new',initializing?[]:unexpected?[1,3]:selected.map(i=>i.id));if(initializing)queueMicrotask(()=>{root=panel('new',selected.map(i=>i.id));});}};
 const win={focus(){},Event:class{},document:{getElementById:()=>menu},ZoteroPane:{selectItems:async ids=>{selected=rows.filter(i=>ids.includes(i.id));},getSelectedItems:()=>selected}};
 const addon={data:{alive:true,initialized:true,version:'3.9.17',standaloneWindow:{focus(){},document:{querySelector:()=>root}}}};
 class NativeItems{}
 const Zotero={LLMForZotero:unavailable?null:addon,getMainWindow:()=>win,Server:{LocalAPI:{Items:NativeItems},Endpoints:{}},Items:{getByLibraryAndKey:(_,key)=>rows[Number(key.at(-1))-1]}};
 const context=vm.createContext({Zotero,Date:{now:()=>now},setTimeout:fn=>{now+=1000;queueMicrotask(fn);}});
 vm.runInContext(fs.readFileSync('../zotero-litradar/llm-context.js','utf8'),context);context.registerLLMContextEndpoint();
 const endpoint=new Zotero.Server.Endpoints['/api/users/:userID/litradar/llm-context']();
 const call=async(method,data,id)=>{const result=await endpoint.run({method,data,libraryID:1,searchParams:new URLSearchParams(id?{request_id:id}:{})});return {code:result[0],body:JSON.parse(result[2])};};
 const flush=async()=>{for(let i=0;i<100;i++)await Promise.resolve();};
 return {context,endpoint,call,flush,NativeItems,addon,commands:()=>commandCount};
}
(async()=>{
 const id='a'.repeat(32),keys=['ITEM0001','ITEM0002'];
 let f=fixture();assert.ok(f.endpoint instanceof f.NativeItems);assert.equal(Object.hasOwn(Object.getPrototypeOf(f.endpoint),'init'),false);
 assert.equal((await f.call('GET')).body.max_items,30);
 assert.equal((await f.call('POST',{request_id:id,keys:['ITEM0001','ITEM0001']})).code,400);
 await f.call('POST',{request_id:id,keys});await f.flush();
 let result=(await f.call('GET',null,id)).body;
 assert.equal(result.status,'attached');assert.deepEqual(result.items.map(i=>i.key),keys);assert.equal(result.conversation_id,'new');
 await f.call('POST',{request_id:id,keys});assert.equal(f.commands(),1);
 assert.equal((await f.call('POST',{request_id:id,keys:['ITEM0001']})).code,409);
 f.context.unregisterLLMContextEndpoint();assert.equal(Object.keys(f.context.Zotero.Server.Endpoints).length,0);
 f=fixture({initializing:true});await f.call('POST',{request_id:id,keys});await f.flush();result=(await f.call('GET',null,id)).body;
 assert.equal(result.status,'attached');assert.equal(result.conversation_id,'new');
 f=fixture({missing:true});await f.call('POST',{request_id:id,keys});await f.flush();result=(await f.call('GET',null,id)).body;
 assert.equal(result.status,'partial');assert.deepEqual(result.items.map(i=>i.status),['attached','skipped']);
 f=fixture({old:true});await f.call('POST',{request_id:id,keys});await f.flush();result=(await f.call('GET',null,id)).body;
 assert.equal(result.status,'unavailable','Old conversation is not a receipt');
 f=fixture({unexpected:true});await f.call('POST',{request_id:id,keys});await f.flush();assert.equal((await f.call('GET',null,id)).body.status,'failed');
 f=fixture({unavailable:true});assert.equal((await f.call('POST',{request_id:id,keys})).code,503);assert.equal(f.commands(),0);
 f=fixture();f.addon.data.version='3.10.0';assert.equal((await f.call('GET')).code,503);
 f=fixture();const request=f.call('POST',{request_id:id,keys});f.context.unregisterLLMContextEndpoint();await request;await f.flush();assert.equal(f.commands(),0);
 console.log('PASS: native auth inheritance, exact fresh conversation, receipts, idempotency, version checks, shutdown');
})().catch(error=>{console.error(error);process.exitCode=1;});

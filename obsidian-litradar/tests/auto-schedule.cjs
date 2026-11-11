const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const ts = require('../node_modules/typescript');
const cache = {}, timers = new Map();
let timerId = 0, now = Date.now();
class Clock extends Date { static now() { return now; } }
const window = {
  setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
  setInterval: callback => { timers.set(++timerId, callback); return timerId; },
  clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id),
};
function load(name) {
  if (cache[name]) return cache[name];
  const exports = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(`src/${name}.ts`, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2018 },
  }).outputText, { exports, window, Date: Clock, require: dep => dep === 'obsidian'
    ? { Plugin: class {}, Notice: class {} } : ['./reading','./types','./store'].includes(dep) ? load(dep.slice(2)) : {} });
  return cache[name] = exports;
}
const Plugin = load('main').default;
function setup(saved) {
  const p = new Plugin();
  p.settings = { ...load('types').DEFAULT_SETTINGS, ...JSON.parse(JSON.stringify(saved || {})) };
  p.refreshViews = () => {};
  p.saveData = async data => { p.persisted = JSON.parse(JSON.stringify(data)); };
  p.engineBlocker = () => null;
  p.today = () => '2026-11-05';
  p.reader = { listRunIds: async () => ['2026-11-04'],
    readStore: async () => ({scan_at:'2026-11-04T08:00:00',journals:[{status:'ok'}]}),
    readSchedule: async () => ({time:'00:00'}), findReport: async () => null };
  p.actions = [];
  p.runAction = async action => { p.actions.push(action); p.lastOutcome = {kind:'ok'}; };
  p.startAutoRun();
  return p;
}
(async () => {
  const p = setup();
  const count = timers.size;
  p.startAutoRun(); assert.equal(timers.size,count,'One timer pair per plugin');
  await p.updateSettings({autoRunEnabled:false});
  await p.maybeAutoRun(); assert.equal(p.actions.length,0);
  await p.updateSettings({autoRunEnabled:true});
  await Promise.all([p.maybeAutoRun(),p.maybeAutoRun()]);
  assert.deepEqual(p.actions,['run','maintain'],'Daily generation precedes recovery; concurrent polls do not duplicate work');
  assert.equal(p.persisted.autoRunState.status,'done');
  await p.maybeAutoRun(); assert.equal(p.actions.length,2);
  p.stopAutoRun();
  const reopened = setup(p.persisted);
  await reopened.maybeAutoRun(); assert.deepEqual(reopened.actions,[],'Restart must remember completed stages');
  reopened.stopAutoRun();

  const retry = setup();
  retry.runAction = async action => { retry.actions.push(action); retry.lastOutcome={kind:'all-failed',message:'抓取失败'}; };
  await retry.maybeAutoRun();
  assert.equal(retry.settings.autoRunState.status,'waiting');
  await retry.maybeAutoRun(); assert.equal(retry.actions.length,1,'No retry before backoff');
  for(let i=0;i<2;i++) { now=retry.settings.autoRunState.retryAt+1; await retry.maybeAutoRun(); }
  assert.equal(retry.actions.length,3);
  assert.equal(retry.settings.autoRunState.status,'blocked');
  await retry.maybeAutoRun(); assert.equal(retry.actions.length,3,'Bound retries per stage');
  retry.runAction=async action=>{retry.actions.push(action);retry.lastOutcome={kind:'ok'};};
  await retry.resumeAutoRun(); assert.equal(retry.settings.autoRunState.status,'done');
  retry.stopAutoRun();

  const resumed = setup({autoRunState:{day:'2026-11-05',action:'maintain',status:'waiting',attempts:1,retryAt:0,message:'重试回查'}});
  await resumed.maybeAutoRun(); assert.deepEqual(resumed.actions,['maintain'],'Resume only the unfinished stage');
  resumed.stopAutoRun();

  const cancelled = setup();
  cancelled.runAction=async action=>{cancelled.actions.push(action);cancelled.lastOutcome={kind:'cancelled'};};
  await cancelled.maybeAutoRun(); await cancelled.maybeAutoRun();
  assert.deepEqual(cancelled.actions,['run']);
  assert.equal(cancelled.persisted.autoRunState.status,'paused');
  cancelled.stopAutoRun();

  const render = setup();
  render.reader.readStore=async()=>({scan_at:'2026-11-05T08:00:00',journals:[{status:'ok'}]});
  await render.maybeAutoRun(); assert.deepEqual(render.actions,['render','maintain'],'Saved fetch resumes at rendering');
  render.stopAutoRun();

  const crashed = setup({autoRunState:{day:'2026-11-05',action:'run',status:'running',attempts:1,retryAt:0,message:'interrupted'}});
  crashed.reader.readStore=async()=>({scan_at:'2026-11-05T08:00:00',journals:[{status:'ok'}]});
  await crashed.maybeAutoRun();assert.deepEqual(crashed.actions,['render','maintain'],'Restart reuses a completed fetch');
  crashed.stopAutoRun();
  const unloading = setup();
  unloading.runAction=async action=>{unloading.actions.push(action);unloading.unloading=true;unloading.lastOutcome={kind:'ok'};};
  await unloading.maybeAutoRun();assert.deepEqual(unloading.actions,['run'],'An unloaded plugin cannot start another stage');
  unloading.stopAutoRun();

  const offline = setup();
  offline.reader.listRunIds=async()=>{throw new Error('temporary read error');};
  await offline.maybeAutoRun(); assert.equal(offline.autoChecking,false,'Read failures release the concurrency guard');
  offline.stopAutoRun();

  const config = setup(); config.stopAutoRun(); config.bridge={};
  const saved=[];
  config.auxiliaryProbe=async args=>{saved.push(args);return {code:0};};
  await Promise.all([config.engineConfigSet('direction','A'),config.engineConfigSet('llm.model','B')]);
  assert.deepEqual(saved.map(args=>args[2]),['direction','llm.model']);
  assert.equal(await config.saveApiKeyEnv('sk-test-secret'),false);
  assert.equal(saved.length,2,'Pasted keys must never be sent as variable names');
  assert.equal(await config.saveApiKeyEnv(' MY_MODEL_KEY '),true);
  assert.equal(config.settings.apiKeyEnv,'MY_MODEL_KEY');
  config.auxiliaryProbe=async()=>({code:1});
  assert.equal(await config.saveApiKeyEnv('NEW_KEY'),false);
  assert.equal(config.settings.apiKeyEnv,'MY_MODEL_KEY');
  assert.equal(timers.size,0);
  console.log('PASS: persisted stages, bounded retries, cancellation, restart, concurrent polling and serialized config writes');
})().catch(error=>{console.error(error);process.exitCode=1;});

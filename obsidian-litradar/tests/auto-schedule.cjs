const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../node_modules/typescript');
const cache = {};
const timers = new Map();
let timerId = 0;
const window = {
  setTimeout: callback => {timers.set(++timerId, callback);return timerId;},
  setInterval: callback => {timers.set(++timerId, callback);return timerId;},
  clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id),
};
function load(name) {
  if (cache[name]) return cache[name];
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(`src/${name}.ts`, 'utf8'), {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2018},
  }).outputText;
  vm.runInNewContext(source, {exports,window,require:dep => dep==='obsidian'
    ? {Plugin:class {},Notice:class {}} : ['./reading','./types'].includes(dep) ? load(dep.slice(2)) : {}});
  return cache[name]=exports;
}
(async()=>{
  const Plugin = load('main').default;
  const plugin = new Plugin();
  plugin.settings={...load('types').DEFAULT_SETTINGS};
  let refreshes=0,runs=0;
  const actions=[];
  plugin.refreshViews=()=>refreshes++;
  plugin.saveData=async()=>{};
  plugin.engineBlocker=()=>null;
  plugin.today=()=>'2026-11-05';
  plugin.reader={listRunIds:async()=>['2026-11-04'],readStore:async()=>({scan_at:'2026-11-04T08:00:00'}),readSchedule:async()=>({time:'00:00'})};
  plugin.runAction=async action=>{actions.push(action);if(action==='run')runs++;};
  plugin.startAutoRun();
  assert.equal(timers.size,2);
  plugin.startAutoRun();
  assert.equal(timers.size,2,'Only one timer pair may run');
  await plugin.updateSettings({autoRunEnabled:false});
  assert.equal(timers.size,0,'Disabling cancels both startup and polling timers');
  await plugin.maybeAutoRun();
  assert.equal(runs,0);
  await plugin.updateSettings({autoRunEnabled:true});
  assert.equal(timers.size,2);
  await plugin.maybeAutoRun();
  await plugin.maybeAutoRun();
  assert.equal(runs,1,'Due job runs at most once per day');
  assert.deepEqual(actions,['maintain','run'],'Recovery precedes today pipeline');
  plugin.stopAutoRun();
  assert.equal(timers.size,0);
  plugin.bridge={probeJson:async()=>({code:1})};
  const before=refreshes;
  assert.equal(await plugin.engineConfigSet('schedule.time','09:30'),false);
  assert.equal(refreshes,before,'Failed saves must not report an updated schedule');
  plugin.bridge={probeJson:async()=>({code:0})};
  assert.equal(await plugin.engineConfigSet('schedule.time','09:30'),true);
  assert.equal(refreshes,before+1);
  console.log('PASS: timer deduplication, enable/disable cleanup, due daily run, config error handling and plan refresh');
})().catch(error=>{console.error(error);process.exitCode=1;});

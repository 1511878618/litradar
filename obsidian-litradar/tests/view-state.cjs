// Run with node tests/view-state.cjs from obsidian-litradar.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../node_modules/typescript');
class Input {
  constructor(value = 'saved') {
    this.dataset = {}; this.classList = { contains: () => false }; this.tagName = 'INPUT'; this.className = 'model'; this.type = 'text';
    this.value = value; this.defaultValue = 'saved';
    this.selectionStart = 2; this.selectionEnd = 4;
  }
  focus() { document.activeElement = this; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
}
class Textarea extends Input {}
class Select {}
const document = { activeElement: null };
const exportsObject = {};
const context = {
  exports: exportsObject, document,
  HTMLInputElement: Input, HTMLTextAreaElement: Textarea, HTMLSelectElement: Select,
  require: name => name === 'obsidian' ? { ItemView: class {} } : {},
};
const source = ts.transpileModule(fs.readFileSync('src/view.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2018 },
}).outputText;
vm.runInNewContext(source, context);
const view = new exportsObject.LitradarView({}, {});
let detail = { dataset: { stateKey: 'article:doi-a' }, open: true };
let control = new Input('unsaved draft');
document.activeElement = control;
const container = {
  scrollTop: 320, scrollLeft: 25,
  querySelectorAll: selector => selector === 'details' ? [detail] : [control],
};
view.contentEl = container;
view.renderContent = () => {
  detail = { dataset: { stateKey: 'article:doi-a' }, open: false };
  control = new Input('new server default');
  container.scrollTop = 0; container.scrollLeft = 0;
};
view.render();
assert.equal(detail.open, true);
assert.equal(control.value, 'unsaved draft');
assert.equal(document.activeElement, control);
assert.equal(control.selectionStart, 2);
assert.equal(control.selectionEnd, 4);
assert.equal(container.scrollTop, 320);
assert.equal(container.scrollLeft, 25);
// Unedited controls should receive refreshed settings rather than stale defaults.
control.value = control.defaultValue;
document.activeElement = null;
view.render();
assert.equal(control.value, 'new server default');
console.log('PASS: expansion, scroll, input draft, focus, selection, fresh defaults');
// A new leaf restores browsing preferences, deferring scroll until data is loaded.
const reopened = new exportsObject.LitradarView({}, {experience:{data:{panel:{panelTab:'library',query:'MRI',journal:'Nature',articleKind:'journal',relevantOnly:true,
 archiveDays:30,archiveQuery:'cardiac',archiveStatus:'Read',archiveJournal:'Nature',archiveKind:'journal',archiveTopic:'EHR',archiveLimit:150,libraryTab:'Read',
 selectedRunId:null,scrollTop:450,expanded:['cross-batch-library','article:doi-a']}}}});
assert.equal(reopened.panelTab,'library');assert.equal(reopened.archiveQuery,'cardiac');assert.equal(reopened.archiveDays,30);
assert.equal(reopened.archiveLimit,150);assert.equal(reopened.restoreScroll,450);assert.equal(reopened.restoreExpanded.length,2);
// A saved filter must redraw even when the data refresh has no new snapshot.
class FilterElement {
  constructor(tag='div',props={}) {this.tag=tag;this.text=props.text||'';this.value=props.value||'';this.dataset={};this.children=[];}
  createEl(tag,props={}) {const el=new FilterElement(tag,props);this.children.push(el);return el;}
  createDiv(props={}) {return this.createEl('div',props);}
  all() {return this.children.flatMap(el=>[el,...el.all()]);}
}
const filterStore={data:{filters:[{name:'MRI',state:{...reopened.plugin.experience.data.panel,panelTab:'today',query:'MRI'}}]},save(){}};
const filtered=new exportsObject.LitradarView({}, {experience:filterStore});
let renderedQuery=null;
filtered.render=()=>{renderedQuery=filtered.query;};
filtered.refresh=async()=>{};
const filterControls=new FilterElement();filtered.renderSavedFilters(filterControls);
const savedSelect=filterControls.all().find(el=>el.tag==='select');
savedSelect.value='MRI';savedSelect.onchange();
assert.equal(renderedQuery,'MRI','Applying conditions must render independently of data changes');
const rebuilt=new FilterElement();filtered.renderSavedFilters(rebuilt);
assert.equal(rebuilt.all().find(el=>el.tag==='select').value,'MRI');
rebuilt.all().find(el=>el.text==='删除选中的筛选').onclick();
assert.equal(filterStore.data.filters.length,0,'The selected saved filter remains removable after redraw');
console.log('PASS: saved filter applies immediately and remains removable after redraw');

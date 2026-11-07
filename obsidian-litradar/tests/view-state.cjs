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

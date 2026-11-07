const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../node_modules/typescript');
const cache = {};
function load(name) {
  if (cache[name]) return cache[name];
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(`src/${name}.ts`, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2018 },
  }).outputText;
  vm.runInNewContext(source, {
    exports,
    require: dep => dep === 'obsidian' ? { Plugin: class {}, ItemView: class {}, Notice: class {} }
      : ['./reading', './types'].includes(dep) ? load(dep.slice(2)) : {},
  });
  return cache[name] = exports;
}
(async () => {
  const { articleKey, normalizeLibrary, matchesArticle, findReadingEntry } = load('reading');
  const article = { id: 'one', doi: 'https://doi.org/10.1234/ABC', title: 'Cardiac MRI prediction', journal: 'Nature', abstract_raw: 'Sex differences in adults', authors: ['Alice'] };
  const duplicate = { ...article, id: 'two', doi: '10.1234/abc' };
  assert.equal(articleKey(article), articleKey(duplicate));
  assert.equal(articleKey({ id: 'fallback' }), 'fallback');
  const filters = { query: 'MRI sex', journal: 'Nature', kind: 'journal', relevantOnly: true };
  assert.equal(matchesArticle(article, filters, false, true), true);
  assert.equal(matchesArticle(article, filters, false, false), false);
  assert.equal(matchesArticle(article, filters, true, true), false);
  assert.equal(matchesArticle(article, { ...filters, query: 'missing' }, false, true), false);
  assert.equal(matchesArticle(article, { ...filters, journal: 'Other' }, false, true), false);
  assert.equal(matchesArticle(article, { ...filters, query: 'alice 10.1234' }, false, true), true);
  assert.equal(Object.keys(normalizeLibrary({ invalid: { status: 'bad' } })).length, 0);
  const noDoi = { ...article, doi: null, id: 'source-id' };
  const normalized = { article: { ...noDoi, id: 'zotero-normalized-id' }, status: 'In Progress' };
  assert.equal(findReadingEntry([normalized], noDoi).status, 'In Progress');
  assert.equal(findReadingEntry([normalized, { ...normalized, article: { ...noDoi, id:'another-id' } }], noDoi), undefined);
  const Plugin = load('main').default;
  const plugin = new Plugin();
  plugin.settings = { ...load('types').DEFAULT_SETTINGS, readingLibrary: {} };
  let persisted;
  const remote = new Map();
  let fail = false;
  let stateWrites = 0;
  let lastArgs;
  plugin.saveData = async data => { persisted = JSON.parse(JSON.stringify(data)); };
  plugin.refreshViews = () => {};
  plugin.zoteroCommand = async args => {
    if (args[args.indexOf("--action")+1] !== "list") lastArgs = args;
    const action = args[args.indexOf('--action') + 1];
    if (action === 'list') return { items: [...remote.values()], statuses: ['New','To Read','In Progress','Read','自定义'].map(value=>({value,label:value})), legacyCount: 0 };
    stateWrites++;
    if (fail) throw new Error('Zotero rejected write');
    const article = JSON.parse(args[args.indexOf('--article-json') + 1]);
    const status = args[args.indexOf('--status') + 1];
    if (!status || status === 'none') remote.delete(articleKey(article));
    else remote.set(articleKey(article), { article, status });
    return { status };
  };
  await plugin.setReadingStatus(article, 'To Read');
  assert.equal(plugin.zoteroLibrary[0].status, 'To Read');
  assert.equal(persisted, undefined, 'New reading states must be owned by Zotero');
  await Promise.all([
    plugin.setReadingStatus(duplicate, 'Read'),
    plugin.setReadingStatus({ ...article, doi: null, id: 'third' }, 'todo'),
  ]);
  assert.equal(remote.size, 2);
  assert.equal(remote.get(articleKey(article)).status, 'Read');
  fail = true;
  await assert.rejects(plugin.setReadingStatus(article, 'none'), /Zotero rejected/);
  assert.equal(remote.get(articleKey(article)).status, 'Read');
  fail = false;
  await plugin.setReadingStatus(article, 'none');
  assert.equal(remote.size, 1);
  plugin.settings.readingLibrary = normalizeLibrary({ old: { article, status: 'todo' } });
  fail = true;
  await assert.rejects(plugin.migrateReadingLibrary(), /Zotero rejected/);
  assert.equal(Object.keys(plugin.settings.readingLibrary).length, 1);
  fail = false;
  await plugin.migrateReadingLibrary();
  assert.equal(Object.keys(persisted.readingLibrary).length, 0);
  assert.equal(remote.get(articleKey(article)).status, 'To Read');
  plugin.zoteroLibrary = [];
  await plugin.refreshZoteroReading(true);
  assert.equal(plugin.zoteroLibrary.length, 2);
  assert.ok(stateWrites > 0);
  await plugin.setReadingStatus(article,'Read','2026-11-04');
  assert.equal(lastArgs[lastArgs.indexOf('--collection-date')+1],'2026-11-04');
  await plugin.setReadingStatus(article,'In Progress');
  assert.equal(lastArgs.includes('--collection-date'),false);

  const View = load('view').LitradarView;
  let preferred;
  const data = { activeRunId: 'old', initialized: true };
  const view = new View({}, { zoteroLibrary: [], zoteroError: '', refreshZoteroReading: async () => {}, today: () => '2026-11-05', reader: { loadPanelData: async (today, id) => { preferred = id; return data; } } });
  view.selectedRunId = '2026-11-01';
  let renders = 0;
  view.render = () => renders++;
  await view.refresh();
  assert.equal(preferred, '2026-11-01');
  await view.refresh();
  assert.equal(renders, 1);
  view.selectedRunId = null;
  await view.refresh();
  assert.equal(preferred, null);
  console.log('PASS: filters, DOI identity, Zotero-owned states, queued writes, migration/failure recovery, remote refresh, history selection, unchanged refresh');
})().catch(error => { console.error(error); process.exitCode = 1; });

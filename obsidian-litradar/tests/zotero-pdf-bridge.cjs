const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
(async () => {
  let calls = 0;
  let pdf = null;
  const item = { deleted: false, isRegularItem: () => true, getBestAttachment: async () => pdf };
  class NativeItems { /* Authentication remains inherited from Zotero's real endpoint. */ }
  const Zotero = {
    initializationPromise: Promise.resolve(),
    Server: { LocalAPI: { Items: NativeItems }, Endpoints: {} },
    Items: { getByLibraryAndKey: (libraryID, key) => key === 'ITEMKEYA' ? item : null },
    Attachments: { addAvailableFiles: async items => { assert.equal(items[0], item); calls++; } },
    logError: error => { throw error; },
  };
  const context = { Zotero, Services: { scriptloader: { loadSubScript() {
    vm.runInContext(fs.readFileSync('../zotero-litradar/reading-list.js','utf8'),context);
  } } } };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('../zotero-litradar/bootstrap.js', 'utf8'), context);
  await context.startup({rootURI:"test/"});
  const Endpoint = Zotero.Server.Endpoints['/api/users/:userID/litradar/pdf'];
  const endpoint = new Endpoint();
  assert.ok(endpoint instanceof NativeItems);
  assert.equal(endpoint.supportedMethods.join(), 'POST');
  assert.equal(Object.hasOwn(Endpoint.prototype, 'init'), false, 'Native authentication must not be bypassed');
  assert.equal((await endpoint.run({ data: { key: '../invalid' } }))[0], 400);
  assert.equal((await endpoint.run({ data: { key: 'MISSINGA' }, libraryID: 1 }))[0], 404);
  assert.equal(calls, 0);
  pdf = { attachmentContentType: 'application/pdf' };
  assert.equal((await endpoint.run({ data: { key: 'ITEMKEYA' }, libraryID: 1 }))[0], 200);
  assert.equal(calls, 0);
  pdf = null;
  assert.equal((await endpoint.run({ data: { key: 'ITEMKEYA' }, libraryID: 1 }))[0], 202);
  assert.equal(calls, 1);
  context.shutdown();
  assert.equal(Object.keys(Zotero.Server.Endpoints).length, 0);
  console.log('PASS: PDF bridge native authorization inheritance, validation, existing PDF, native download delegation, cleanup');
})().catch(error => { console.error(error); process.exitCode = 1; });

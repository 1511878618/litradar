/* Zotero 10.0: reuse the native local API's server-ID and write-key authorization. */
var endpointPath = "/api/users/:userID/litradar/pdf";
var endpointClass;
var downloading = new Set();

async function startup({ rootURI }) {
  await Zotero.initializationPromise;
  Services.scriptloader.loadSubScript(rootURI + "reading-list.js", this);
  registerReadingEndpoint();
  endpointClass = class extends Zotero.Server.LocalAPI.Items {
    supportedMethods = ["POST"];
    supportedDataTypes = ["application/json"];
    permitBookmarklet = false;

    async run(request) {
      const key = request.data && request.data.key;
      if (typeof key !== "string" || !/^[A-Z0-9]{8}$/.test(key)) {
        return [400, "application/json", JSON.stringify({ error: "Invalid item key" })];
      }
      const item = Zotero.Items.getByLibraryAndKey(request.libraryID, key);
      if (!item || item.deleted || !item.isRegularItem()) {
        return [404, "application/json", JSON.stringify({ error: "Item not found" })];
      }
      const best = await item.getBestAttachment();
      if (best && best.attachmentContentType === "application/pdf") {
        return [200, "application/json", JSON.stringify({ message: "Zotero 中已有 PDF，可直接打开" })];
      }
      if (!downloading.has(key)) {
        downloading.add(key);
        // Zotero displays its native progress queue and resolves eligible files.
        // Nothing is downloaded or attached by the Obsidian plugin.
        void Zotero.Attachments.addAvailableFiles([item])
          .catch(error => Zotero.logError(error))
          .finally(() => downloading.delete(key));
      }
      return [202, "application/json", JSON.stringify({
        accepted: true, message: "已交给 Zotero 查找和下载 PDF；请在 Zotero 查看下载进度与结果"
      })];
    }
  };
  Zotero.Server.Endpoints[endpointPath] = endpointClass;
}

function shutdown() {
  if (typeof unregisterReadingEndpoint === "function") unregisterReadingEndpoint();
  if (Zotero.Server.Endpoints[endpointPath] === endpointClass) {
    delete Zotero.Server.Endpoints[endpointPath];
  }
}
function install() {}
function uninstall() {}

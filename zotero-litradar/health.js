/* Read-only diagnostics and local attachment availability. No file paths returned. */
var healthPath = "/api/users/:userID/litradar/health";
var filesPath = "/api/users/:userID/litradar/files";
var healthClass;
var filesClass;

function registerHealthEndpoints(version) {
  healthClass = class extends Zotero.Server.LocalAPI.Items {
    supportedMethods = ["GET"];
    permitBookmarklet = false;
    async run() {
      const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
      const reading = await AddonManager.getAddonByID("reading-list@hotmail.com");
      const llm = await AddonManager.getAddonByID("zotero-llm@github.com.yilewang");
      let readingReady = false, llmReady = false, llmError = "";
      try { readingOverlay(); readingReady = true; } catch (_) {}
      try { llmContextHost(); llmReady = true; } catch (error) { llmError = String(error.message || error); }
      return [200, "application/json", JSON.stringify({
        bridgeVersion: version, zoteroVersion: Zotero.version,
        reading: { installed: !!reading, enabled: !!reading?.isActive, version: reading?.version || "", ready: readingReady },
        llm: { installed: !!llm, enabled: !!llm?.isActive, version: llm?.version || "", ready: llmReady, error: llmError }
      })];
    }
  };
  filesClass = class extends Zotero.Server.LocalAPI.Items {
    supportedMethods = ["GET"];
    permitBookmarklet = false;
    async run(request) {
      const key = request.searchParams?.get("key");
      if (!/^[A-Z0-9]{8}$/.test(key || "")) return [400, "application/json", JSON.stringify({ error: "Invalid item key" })];
      const item = Zotero.Items.getByLibraryAndKey(request.libraryID, key);
      if (!item || item.deleted || !item.isRegularItem()) return [404, "application/json", JSON.stringify({ error: "Item not found" })];
      const files = [];
      for (const id of item.getAttachments()) {
        const attachment = await Zotero.Items.getAsync(id);
        if (attachment.attachmentContentType !== "application/pdf") continue;
        files.push({ key: attachment.key, available: await attachment.fileExists() });
      }
      const best = await item.getBestAttachment();
      return [200, "application/json", JSON.stringify({ key, downloading: downloading.has(key), files,
        contextAvailable: !!best && await best.fileExists() })];
    }
  };
  Zotero.Server.Endpoints[healthPath] = healthClass;
  Zotero.Server.Endpoints[filesPath] = filesClass;
}
function unregisterHealthEndpoints() {
  if (Zotero.Server.Endpoints[healthPath] === healthClass) delete Zotero.Server.Endpoints[healthPath];
  if (Zotero.Server.Endpoints[filesPath] === filesClass) delete Zotero.Server.Endpoints[filesPath];
}

/* Reading List 1.7.x adapter, loaded into the bridge's bootstrap scope. */
var readingEndpointPath = "/api/users/:userID/litradar/reading";
var readingEndpointClass;
var legacyReadingTags = ["litradar/待读", "litradar/待读", "litradar/已读"];

function readingOverlay() {
  const overlay = Zotero.ZoteroReadingList?.data?.zoteroReadingListOverlay;
  if (!overlay || !Array.isArray(overlay.statusNames) ||
      typeof overlay.getItemReadStatus !== "function" || typeof overlay.setItemReadStatus !== "function") {
    throw new Error("请安装并启用 Zotero Reading List（已验证 1.7.1），然后重启 Zotero");
  }
  return overlay;
}

function readingOptions(overlay) {
  return overlay.statusNames.map((name, index) => ({
    value: name, label: `${overlay.statusIcons?.[index] || ""} ${name}`.trim()
  }));
}

async function findReadingItems(libraryID, field, value) {
  const search = new Zotero.Search();
  search.libraryID = libraryID;
  search.addCondition(field, "contains", value);
  search.addCondition("deleted", "false");
  const ids = await search.search();
  const items = await Zotero.Items.getAsync(ids);
  return items.filter(item => item.isRegularItem() && !item.deleted);
}

function readingResponseItem(item, overlay) {
  return { key: item.key, version: item.clientVersion, data: item.toJSON(),
    readingStatus: overlay.getItemReadStatus(item) || "" };
}

async function writeReadingStatus(item, status, overlay, clear = false, version = undefined) {
  try {
  await Zotero.DB.executeTransaction(async () => {
    if (version !== undefined && version !== item.clientVersion) {
      const error = new Error("Item changed; refresh and retry"); error.httpStatus = 412; throw error;
    }
    const originalTags = item.getTags();
    const statusTags = new Set(overlay.statusNames.flatMap(name => [name, overlay.formatTag(name)]));
    if (clear) {
      // Reading List's clear method starts an unawaited saveTx(), so clear its
      // exact fields here and await our own transaction instead.
      const extra = item.getField("extra");
      item.setField("extra", extra.split(/\r?\n/).filter(line => !/^Read_Status(?:_Date)?:/i.test(line)).join("\n"));
      // Only remove exact status tags, preserving unrelated project tags.
      if (Zotero.Prefs.get("extensions.zotero.zotero-reading-list.tag-synchronisation", true)) {
        for (const tag of item.getTags()) if (statusTags.has(tag.tag)) item.removeTag(tag.tag);
      }
    } else {
      // false prevents the plugin's fire-and-forget saveTx(); we await item.save().
      overlay.setItemReadStatus(item, status, false);
    }
    // Reading List's tag cleaner uses substring matches; retain unrelated tags.
    for (const tag of originalTags) {
      if (!statusTags.has(tag.tag) && !legacyReadingTags.includes(tag.tag)) item.addTag(tag.tag, tag.type || 0);
    }
    for (const tag of legacyReadingTags) item.removeTag(tag);
    await item.save({ undoAction: "undo-action-edit-metadata", undoActionArgs: { count: 1 } });
  });
  } catch(error) {
    // A rolled-back save must not leave unsaved status/tags in Zotero's cache.
    try { await item.reload(["primaryData", "itemData", "tags"], true); }
    catch(reloadError) { Zotero.logError(reloadError); }
    throw error;
  }
}

function registerReadingEndpoint() {
  readingEndpointClass = class extends Zotero.Server.LocalAPI.Items {
    supportedMethods = ["GET", "POST"];
    supportedDataTypes = ["application/json"];
    permitBookmarklet = false;

    async run(request) {
      try {
        const overlay = readingOverlay();
        if (request.method === "GET") {
          if (request.searchParams?.get("metadata") === "1") {
            return [200, "application/json", JSON.stringify({provider:"reading-list",bridgeVersion:"0.2.0",statuses:readingOptions(overlay)})];
          }
          const items = await findReadingItems(request.libraryID, "extra", "Read_Status:");
          const legacy = new Set();
          for (const tag of legacyReadingTags) {
            for (const item of await findReadingItems(request.libraryID, "tag", tag)) {
              if (item.getTags().some(value => legacyReadingTags.includes(value.tag))) legacy.add(item.key);
            }
          }
          return [200, "application/json", JSON.stringify({ provider: "reading-list", bridgeVersion: "0.2.0",
            statuses: readingOptions(overlay), legacyCount: legacy.size,
            entries: items.filter(item => !!overlay.getItemReadStatus(item)).map(item => readingResponseItem(item, overlay)) })];
        }
        const body = request.data || {};
        if (body.action === "migrate") {
          if (!Array.isArray(body.items) || body.items.length > 1000) return [400, "text/plain", "Invalid migration batch"];
          for (const row of body.items) {
            if (!/^[A-Z0-9]{8}$/.test(row.key) || !overlay.statusNames.includes(row.status)) return [400, "text/plain", "Invalid migration status or key"];
          }
          const result = { migrated: 0, preserved: 0, failed: [] };
          for (const row of body.items) {
            const item = Zotero.Items.getByLibraryAndKey(request.libraryID, row.key);
            if (!item || item.deleted || !item.isRegularItem()) { result.failed.push({key:row.key,error:"Item not found"}); continue; }
            if (row.version !== undefined && row.version !== item.clientVersion) { result.failed.push({key:row.key,error:"Item changed; refresh and retry"}); continue; }
            const oldTags = item.getTags().map(tag => tag.tag);
            if (!oldTags.some(tag => legacyReadingTags.includes(tag))) continue;
            try {
              const current = overlay.getItemReadStatus(item);
              await writeReadingStatus(item, current || row.status, overlay, false, row.version);
              if (current) result.preserved++; else result.migrated++;
            } catch(error) { result.failed.push({key:row.key,error:String(error)}); }
          }
          return [200, "application/json", JSON.stringify(result)];
        }
        if (typeof body.key !== "string" || !/^[A-Z0-9]{8}$/.test(body.key)) return [400, "text/plain", "Invalid item key"];
        if (!body.clear && !overlay.statusNames.includes(body.status)) return [400, "text/plain", "Reading List 状态不存在，请刷新状态选项"];
        const item = Zotero.Items.getByLibraryAndKey(request.libraryID, body.key);
        if (!item || item.deleted || !item.isRegularItem()) return [404, "text/plain", "Item not found"];
        if (body.version !== undefined && body.version !== item.clientVersion) return [412, "text/plain", "Item changed; refresh and retry"];
        await writeReadingStatus(item, body.status, overlay, body.clear === true, body.version);
        return [200, "application/json", JSON.stringify(readingResponseItem(item, overlay))];
      } catch(error) {
        Zotero.logError(error);
        return [error.httpStatus || 503, "application/json", JSON.stringify({error:String(error)})];
      }
    }
  };
  Zotero.Server.Endpoints[readingEndpointPath] = readingEndpointClass;
}

function unregisterReadingEndpoint() {
  if (Zotero.Server.Endpoints[readingEndpointPath] === readingEndpointClass) delete Zotero.Server.Endpoints[readingEndpointPath];
}

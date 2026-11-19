/* Version-scoped UI adapter: use llm-for-zotero's own add-context command.
 * Never run an agent, send a message, read provider settings or scrape answers.
 * The verified adapter is 3.9.17; fail closed if its menu/panel contract changes.
 */
var llmContextPath = "/api/users/:userID/litradar/llm-context";
var llmContextClass;
var llmContextJobs = new Map();
var llmContextBusy = false;
var llmContextGeneration = 0;
var llmContextLimit = 30;

function llmContextHost() {
  const addon = Zotero.LLMForZotero;
  const version = String(addon?.data?.version || "");
  if (!addon?.data?.alive || !addon.data.initialized) throw new Error("请启用 llm-for-zotero 并等待它初始化完成");
  if (version!=="3.9.17") throw new Error(`llm-for-zotero ${version || "未知版本"} 尚不受此上下文适配器支持（已验证 3.9.17）`);
  const win = Zotero.getMainWindow();
  const menu = win?.document.getElementById("llmforzotero-add-items-as-context");
  if (!win?.ZoteroPane?.selectItems || !menu || typeof menu.dispatchEvent !== "function") {
    throw new Error("未找到 llm-for-zotero 添加上下文入口，请更新兼容的插件版本并重新打开 Zotero 主窗口");
  }
  return {addon, version, win, menu};
}

function llmContextPanel(addon) {
  const win = addon.data.standaloneWindow;
  if (!win || win.closed) return null;
  return win.document.querySelector('#llm-main[data-standalone="true"]');
}

function llmContextResponse(status, body) {
  return [status, "application/json", JSON.stringify(body)];
}

async function llmContextInspect(libraryID, keys) {
  const rows = [];
  for (const key of keys) {
    const item = Zotero.Items.getByLibraryAndKey(libraryID, key);
    if (!item || item.deleted || !item.isRegularItem()) throw new Error(`文献 ${key} 不存在或已移入回收站，请刷新专题后重试`);
    const row = {key, itemID:item.id, title:item.getField("title"), status:"pending", reason:""};
    const attachment = await item.getBestAttachment();
    if (!attachment) {
      row.status="skipped"; row.reason="没有默认附件，请先在 Zotero 添加 PDF 或其他支持的全文附件";
    } else if (typeof attachment.getFilePathAsync !== "function" || !await attachment.getFilePathAsync()) {
      row.status="skipped"; row.reason="附件文件尚未下载或不可访问，请先在 Zotero 打开附件";
    }
    // Other formats are decided by the upstream command. A visible chip is the receipt.
    rows.push(row);
  }
  return rows;
}

async function llmContextOpen(job, host, generation) {
  try {
    const eligible = job.items.filter(row=>row.status==="pending");
    if (!eligible.length) {job.status="unavailable"; return;}
    const before = llmContextPanel(host.addon)?.dataset.itemId || "";
    host.win.focus();
    await host.win.ZoteroPane.selectItems(eligible.map(row=>row.itemID));
    if (generation!==llmContextGeneration) throw new Error("桥接已关闭，请重新操作");
    const selected = new Set((host.win.ZoteroPane.getSelectedItems() || []).map(item=>item.id));
    if (selected.size!==eligible.length || eligible.some(row=>!selected.has(row.itemID))) {
      throw new Error("未能准确选中请求的文献，未向聊天添加任何上下文");
    }
    host.menu.dispatchEvent(new host.win.Event("command", {bubbles:true}));
    const deadline = Date.now()+20000;
    while (Date.now()<deadline && generation===llmContextGeneration) {
      const root = llmContextPanel(host.addon);
      const conversation = root?.dataset.itemId || "";
      if (conversation && conversation!==before && root.dataset.conversationKind==="global" &&
          Number(root.dataset.libraryId)===job.libraryID) {
        if (job.conversation_id && job.conversation_id!==conversation) throw new Error("分析会话已切换，请在 Zotero 核对附加文献");
        const ids = new Set(Array.from(root.querySelectorAll(".llm-paper-context-chip[data-paper-item-id]"))
          .map(chip=>Number(chip.dataset.paperItemId)));
        if ([...ids].some(id=>!eligible.some(row=>row.itemID===id))) throw new Error("会话上下文出现其他文献，请在 Zotero 核对后继续");
        // The standalone window mounts an empty conversation before the native
        // command prepares its fresh target. Bind only when context is visible.
        if (ids.size) job.conversation_id=conversation;
        for (const row of eligible) {
          row.status=ids.has(row.itemID)?"attached":"pending";
          row.reason=ids.has(row.itemID)?"已加入会话上下文，尚未发送消息或读取全文":"";
        }
        if (eligible.every(row=>row.status==="attached")) break;
      }
      await new Promise(resolve=>setTimeout(resolve,150));
    }
    if (generation!==llmContextGeneration) throw new Error("桥接已关闭，请在 Zotero 核对上下文");
    for (const row of job.items) if (row.status==="pending") {
      row.status="unconfirmed";row.reason="未确认附加成功：可能是附件格式不支持或会话未就绪，请在 Zotero 核对";
    }
    const attached=job.items.filter(row=>row.status==="attached").length;
    job.status=attached===job.items.length?"attached":attached?"partial":"unavailable";
    if (attached) host.addon.data.standaloneWindow?.focus();
  } catch(error) {
    job.status="failed";job.error=String(error.message || error);
  } finally {
    if (generation===llmContextGeneration) llmContextBusy=false;
  }
}

function registerLLMContextEndpoint() {
  llmContextGeneration++;
  llmContextClass = class extends Zotero.Server.LocalAPI.Items {
    supportedMethods=["GET","POST"];
    supportedDataTypes=["application/json"];
    permitBookmarklet=false;

    async run(request) {
      try {
        if (request.method==="GET" && request.searchParams?.get("request_id")) {
          const job=llmContextJobs.get(request.searchParams.get("request_id"));
          return job && job.libraryID===request.libraryID?llmContextResponse(200,job):llmContextResponse(404,{error:"上下文请求不存在，请在 Zotero 检查会话"});
        }
        const host=llmContextHost();
        if (request.method==="GET") return llmContextResponse(200,{provider:"llm-for-zotero",bridgeVersion:"0.3.0",version:host.version,max_items:llmContextLimit});
        const {request_id:requestID,keys}=request.data || {};
        if (!/^[a-f0-9]{32}$/.test(requestID || "") || !Array.isArray(keys) || !keys.length || keys.length>llmContextLimit ||
            keys.some(key=>typeof key!=="string" || !/^[A-Z0-9]{8}$/.test(key)) || new Set(keys).size!==keys.length) {
          return llmContextResponse(400,{error:"请选择 1–30 篇不重复的文献，并提供有效请求标识"});
        }
        const previous=llmContextJobs.get(requestID);
        if (previous) {
          if (previous.libraryID!==request.libraryID || JSON.stringify(previous.keys)!==JSON.stringify(keys)) return llmContextResponse(409,{error:"请求标识已用于其他文献"});
          return llmContextResponse(200,previous);
        }
        if (llmContextBusy) return llmContextResponse(409,{error:"正在附加另一组文献，请稍后再试"});
        llmContextBusy=true;
        const generation=llmContextGeneration;
        let items;
        try {items=await llmContextInspect(request.libraryID,keys);}
        catch(error) {if(generation===llmContextGeneration) llmContextBusy=false;throw error;}
        if (generation!==llmContextGeneration) throw new Error("桥接已关闭，请重试");
        const job={provider:"llm-for-zotero",request_id:requestID,libraryID:request.libraryID,keys,items,status:"opening",conversation_id:""};
        llmContextJobs.set(requestID,job);
        while (llmContextJobs.size>30) llmContextJobs.delete(llmContextJobs.keys().next().value);
        void llmContextOpen(job,host,generation);
        return llmContextResponse(202,job);
      } catch(error) {return llmContextResponse(503,{error:String(error.message || error)});}
    }
  };
  Zotero.Server.Endpoints[llmContextPath]=llmContextClass;
}

function unregisterLLMContextEndpoint() {
  llmContextGeneration++;
  llmContextBusy=false;
  llmContextJobs.clear();
  if (Zotero.Server.Endpoints[llmContextPath]===llmContextClass) delete Zotero.Server.Endpoints[llmContextPath];
}

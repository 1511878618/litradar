/* Test-only helper, installed exclusively into an isolated profile. */
var probeTimer;
var probeBusy = false;
async function startup() {
  setTimeout(async () => {
    try {
      if (Zotero.DataDirectory.dir !== TEST_DATA_DIR) throw new Error("Unexpected test data directory");
      const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
      for (const path of TEST_XPIS) {
        const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile); file.initWithPath(path);
        const install = await AddonManager.getInstallForFile(file);
        if (install.error) throw new Error("Installation validation: " + install.error);
        await new Promise((resolve,reject) => {
          install.addListener({onInstallEnded:resolve,onInstallFailed:value=>reject(new Error("Installation: "+value.error))});
          install.install();
        });
      }
      for (let i=0;i<50 && !Zotero.ZoteroReadingList?.data?.zoteroReadingListOverlay;i++) await new Promise(resolve=>setTimeout(resolve,200));
      if (!Zotero.ZoteroReadingList?.data?.zoteroReadingListOverlay) throw new Error("Reading List not ready");
      // Simulate user permission only in this empty, isolated test profile.
      Zotero.Server.LocalAPI._promptForAuthorization = async()=>({allow:true,remember:true});
      const addon=await AddonManager.getAddonByID("litradar-pdf@litradar.local");
      await Zotero.File.putContentsAsync(TEST_ROOT+"/ready.json",JSON.stringify({version:Services.appinfo.version,bridgeVersion:addon.version,isActive:addon.isActive,isCompatible:addon.isCompatible,readingEndpoint:!!Zotero.Server.Endpoints["/api/users/:userID/litradar/reading"]}));
      probeTimer=setInterval(async()=>{
        if(probeBusy) return;
        probeBusy=true;
        try {
          const file=Zotero.File.pathToFile(TEST_ROOT+"/command.json");
          if(!file.exists()) return;
          const command=JSON.parse(await Zotero.File.getContentsAsync(file)); file.remove(false);
          const overlay=Zotero.ZoteroReadingList.data.zoteroReadingListOverlay;
          let result={};
          if(command.action==="custom") {
            const names=[...overlay.statusNames,"精读候选"];
            const icons=[...overlay.statusIcons,"📘"];
            Zotero.Prefs.set("extensions.zotero.zotero-reading-list.statuses-and-icons-list",names.join(";")+"|"+icons.join(";"),true);
            result={statuses:overlay.statusNames};
          } else {
            const item=Zotero.Items.getByLibraryAndKey(Zotero.Libraries.userLibraryID,command.key);
            if(command.action==="set") { overlay.setItemReadStatus(item,command.status,false); await item.saveTx(); }
            result={status:overlay.getItemReadStatus(item)||""};
          }
          await Zotero.File.putContentsAsync(TEST_ROOT+"/command-result.json",JSON.stringify(result));
        } catch(error) {await Zotero.File.putContentsAsync(TEST_ROOT+"/command-result.json",JSON.stringify({error:String(error),stack:error.stack}));}
        finally {probeBusy=false;}
      },200);
    } catch(error) {await Zotero.File.putContentsAsync(TEST_ROOT+"/ready.json",JSON.stringify({error:String(error),stack:error.stack}));}
  },1500);
}
function shutdown() { if(probeTimer)clearInterval(probeTimer); }
function install() {}
function uninstall() {}

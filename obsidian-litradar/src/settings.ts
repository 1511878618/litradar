/**
 * Settings tab. Only configuration lives here.
 *
 * Model configuration, PDF reading and analysis are owned by llm-for-zotero.
 * Research settings are written by the engine into config.local.yaml.
 */

import { App, Notice, PluginSettingTab, Setting } from "obsidian";
import type LitradarPlugin from "./main";
import { DEFAULT_SETTINGS } from "./types";

export class LitradarSettingTab extends PluginSettingTab {
  constructor(
    app: App,
    private readonly plugin: LitradarPlugin,
  ) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.addClass("litradar-settings");

    const intro = containerEl.createDiv({ cls: "litradar-settings-hero" });
    intro.createDiv({ cls: "litradar-eyebrow", text: "LITRADAR / 偏好设置" });
    intro.createEl("h2", { text: "让文献雷达适合你的节奏" });
    intro.createEl("p", {
      cls: "litradar-settings-intro",
      text: "设置研究方向、每日抓取时间和阅读偏好。文献与日报保存在你的 vault 中。",
    });

    this.renderEngineStatus(containerEl);
    new Setting(containerEl).setName("Zotero 连接状态")
      .setDesc("分别检查 Zotero、Reading List、讨论插件与桥接版本，查看具体处理步骤。")
      .addButton(button=>button.setButtonText("检查连接").onClick(()=>this.plugin.openConnections()));
    const scheduleEl = containerEl.createDiv();
    void this.renderScheduleSettings(scheduleEl);

    const researchEl = containerEl.createDiv({ cls: "litradar-research-settings" });
    void this.renderResearchSettings(researchEl);
    void this.renderProfileBuilder(researchEl);

    new Setting(containerEl).setName("引擎").setHeading();

    new Setting(containerEl)
      .setName("Python 解释器路径")
      .setDesc(
        "必填，绝对路径，不依赖 PATH。填 conda base 的解释器，例如 " +
          "/opt/homebrew/anaconda3/bin/python 或 E:\\conda\\python.exe。插件只会用它执行 `-m litradar`。",
      )
      .addText((text) => {
        text
          .setPlaceholder("E:\\conda\\python.exe")
          .setValue(this.plugin.settings.pythonPath)
          .onChange((value) => {
            void this.plugin.updateSettings({ pythonPath: value.trim() });
          });
        text.inputEl.addClass("litradar-settings-wide");
        text.inputEl.spellcheck = false;
        text.inputEl.autocomplete = "off";
      });

    new Setting(containerEl)
      .setName("Vault 根目录")
      .setDesc(
        `留空 = 当前 vault（${this.plugin.basePath || "无法读取"}）。` +
          "填写绝对路径时引擎会在该目录下找 .litradar；插件仍然只读当前 vault 内的文件。",
      )
      .addText((text) => {
        text
          .setPlaceholder(this.plugin.basePath || "（当前 vault）")
          .setValue(this.plugin.settings.vaultRoot)
          .onChange((value) => {
            void this.plugin.updateSettings({ vaultRoot: value.trim() });
          });
        text.inputEl.addClass("litradar-settings-wide");
        text.inputEl.spellcheck = false;
      });

    new Setting(containerEl)
      .setName("数据目录")
      .setDesc("相对 vault 根目录，默认 .litradar。插件只读取其中的 JSON / Markdown。")
      .addText((text) => {
        text
          .setPlaceholder(".litradar")
          .setValue(this.plugin.settings.storeDir)
          .onChange((value) => {
            void this.plugin.updateSettings({ storeDir: value.trim() || DEFAULT_SETTINGS.storeDir });
          });
        text.inputEl.spellcheck = false;
      });

    new Setting(containerEl)
      .setName("文献分析 · llm-for-zotero")
      .setDesc("在 Zotero 的 llm-for-zotero 设置中管理模型与文件笔记目录。Litradar 将专题文献附加到新对话；问答与 Markdown 保存使用 llm-for-zotero 自己的功能，请手动将输出目录设到 vault 下，无需填写 API key。日报使用本地关键词排序，中文标题翻译仍可单独启用。")
      .addButton(button => button.setButtonText("打开研究专题").onClick(() => this.plugin.openIdeas()));

    new Setting(containerEl).setName("面板").setHeading();

    new Setting(containerEl)
      .setName("自动刷新间隔（秒）")
      .setDesc("面板轮询 .litradar 的间隔；0 = 关闭。默认 30。")
      .addText((text) => {
        text.inputEl.type = "number";
        text.inputEl.min = "0";
        text.inputEl.max = "3600";
        text.inputEl.step = "5";
        text
          .setValue(String(this.plugin.settings.autoRefreshSeconds))
          .onChange((value) => {
            const parsed = Number(value);
            const seconds = Number.isFinite(parsed) ? Math.max(0, Math.min(3600, Math.round(parsed))) : 0;
            void this.plugin.updateSettings({ autoRefreshSeconds: seconds });
          });
      });

    new Setting(containerEl)
      .setName("显示运行日志")
      .setDesc("在面板底部展示 stderr / run.log 的末 20 行（失败时始终展示错误摘要）。")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.showLogTail).onChange((value) => {
          void this.plugin.updateSettings({ showLogTail: value });
        }),
      );

    new Setting(containerEl)
      .setName("最低引擎版本")
      .setDesc(
        "版本握手门槛，默认 2.0.0。低于此值的引擎会禁用所有操作按钮；插件不会自动升级引擎。",
      )
      .addText((text) => {
        text
          .setPlaceholder("2.0.0")
          .setValue(this.plugin.settings.minEngine)
          .onChange((value) => {
            void this.plugin.updateSettings({ minEngine: value.trim() || DEFAULT_SETTINGS.minEngine });
          });
        text.inputEl.spellcheck = false;
      });

    new Setting(containerEl).setName("关于").setHeading();

    const about = containerEl.createDiv({ cls: "litradar-settings-about" });
    about.createEl("p", {
      text:
        `插件版本 ${this.plugin.manifest.version} · 需要 Obsidian 桌面版。` +
        " 引擎升级请自行执行 `pipx upgrade litradar` 或重新 `pip install -U litradar`。",
    });
    about.createEl("p", {
      text:
        "界面与自动任务状态保存在插件设置中；研究设置通过引擎写入配置。" +
        " 日报、文献数据和推荐结果由引擎生成。",
    });

    new Setting(containerEl)
      .setName("打开面板")
      .setDesc("在右侧边栏打开文献雷达面板。")
      .addButton((button) =>
        button.setButtonText("打开面板").onClick(() => {
          void this.plugin.activateView();
        }),
      )
      .addButton((button) =>
        button.setButtonText("重新检测引擎").onClick(async () => {
          await this.plugin.checkEngine(true);
          this.display();
        }),
      );
  }

  /** Zotero → keyword profile: check collections, generate, store under .litradar/profiles/. */
  private async renderProfileBuilder(containerEl: HTMLElement): Promise<void> {
    new Setting(containerEl).setName("科研画像").setHeading();
    containerEl.createEl("p", {
      text:
        "勾选用于生成画像的 Zotero 收藏夹（可多选）。生成后会写入 " +
        ".litradar/profiles/<名称>.yaml 与 .md，并立即成为当前生效的画像。",
    });

    const loaded = await this.plugin.engineCollections();
    const collections = loaded.collections;
    if (!collections) {
      containerEl.createEl("p", {
        text: "读不到 Zotero 收藏夹：" + (loaded.error || "未知原因"),
      });
      containerEl.createEl("p", {
        text: "排查：设置 → Litradar 里点「检测引擎」；确认 Python 路径指向 conda base；" +
          "Zotero 数据库默认在 ~/Zotero/zotero.sqlite。",
      });
      return;
    }

    new Setting(containerEl)
      .setName("文献保存位置")
      .setDesc("默认按日报日期自动创建 Litradar 文献雷达 / YYYY-MM-DD。也可指定其他文件夹；复用已有文献时保留原收藏夹并添加此归属。")
      .addDropdown(dropdown => {
        dropdown.addOption("", "自动归档：Litradar 文献雷达 / 每日日期");
        for (const item of collections) dropdown.addOption(item.key, item.path || item.name);
        dropdown.setValue(this.plugin.settings.zoteroTargetCollection);
        dropdown.onChange(value => { void this.plugin.updateSettings({ zoteroTargetCollection: value }); });
      });

    const selected = new Set(this.plugin.settings.profileCollections);
    const search = containerEl.createEl("input", {
      type: "search",
      placeholder: `筛选收藏夹（共 ${collections.length} 个）…`,
      cls: "litradar-collection-filter",
    });
    const list = containerEl.createDiv({ cls: "litradar-collection-list" });
    for (const item of collections) {
      const label = list.createEl("label", { cls: "litradar-collection-item" });
      const box = label.createEl("input", { type: "checkbox" });
      box.checked = selected.has(item.key) || (selected.has(item.name) && collections.filter(row => row.name === item.name).length === 1);
      box.addEventListener("change", () => {
        selected.delete(item.name);
        if (box.checked) selected.add(item.key);
        else selected.delete(item.key);
        void this.plugin.updateSettings({ profileCollections: Array.from(selected) });
      });
      label.createSpan({ text: item.path || item.name });
    }
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      for (const child of Array.from(list.children)) {
        const element = child as HTMLElement;
        element.style.display = (element.textContent ?? "").toLowerCase().includes(query) ? "" : "none";
      }
    });

    new Setting(containerEl)
      .setName("画像名称")
      .setDesc("决定 .litradar/profiles/ 下的文件名；留空则用收藏夹名。")
      .addText((text) => {
        text
          .setPlaceholder("EHR+心血管")
          .setValue(this.plugin.settings.profileName)
          .onChange((value) => {
            void this.plugin.updateSettings({ profileName: value.trim() });
          });
        text.inputEl.spellcheck = false;
      });

    const status = containerEl.createEl("p", { cls: "litradar-profile-status" });
    new Setting(containerEl)
      .setName("生成画像")
      .setDesc("从所选收藏夹的元数据提取本地关键词画像，不调用模型。需要跨文献归纳时使用研究专题中的 llm-for-zotero 协作。")
      .addButton((button) => {
        button.setButtonText("生成画像").onClick(async () => {
          const names = Array.from(selected);
          if (names.length === 0) {
            new Notice("先勾选至少一个收藏夹");
            return;
          }
          button.setDisabled(true);
          button.setButtonText("生成中…");
          status.setText(`正在读取 ${names.length} 个收藏夹并生成画像…`);
          const result = await this.plugin.engineProfile(names, this.plugin.settings.profileName);
          button.setDisabled(false);
          button.setButtonText("生成画像");
          if (result.ok) {
            new Notice("画像已生成并生效");
            status.setText("已写入 .litradar/profiles/，并更新 profile.yaml。");
            this.display();
          } else {
            new Notice("画像生成失败，请查看面板日志");
            status.setText("生成失败：" + (result.error || "未知原因"));
          }
        });
      });
  }

  /** Engine-side settings: read via `config list --json`, written via `config set`. */
  private async renderResearchSettings(containerEl: HTMLElement): Promise<void> {
    const config = await this.plugin.engineConfig();
    new Setting(containerEl).setName("研究设置").setHeading();
    if (!config) {
      containerEl.createEl("p", {
        text: "读不到引擎配置（引擎未就绪或 Python 路径不对）。修好后回到这里刷新。",
      });
      return;
    }

    const read = (path: string): unknown =>
      path.split(".").reduce<unknown>((node, key) => {
        if (node && typeof node === "object") {
          return (node as Record<string, unknown>)[key];
        }
        return undefined;
      }, config);

    const save = async (key: string, value: string): Promise<void> => {
      const ok = await this.plugin.engineConfigSet(key, value);
      if (!ok) new Notice(`写入失败：${key}`);
    };

    const debounced = (key: string, value: () => string): (() => void) => {
      let timer: number | undefined;
      return (): void => {
        if (timer) window.clearTimeout(timer);
        timer = window.setTimeout(() => void save(key, value()), 600);
      };
    };

    const hint = `引擎设置写到 ${this.plugin.resolvedVaultRoot}/.litradar/config.local.yaml（下次运行生效）。`;
    const useProfile = read("research.use_profile") !== false;
    new Setting(containerEl)
      .setName("研究设置来源")
      .setDesc("画像模式优先使用已生成的 Zotero 画像；手动模式使用配置中的研究方向和关键词。切换不会删除画像或手动设置。")
      .addDropdown(dropdown => dropdown
        .addOption("profile", "使用 Zotero 画像（存在时优先）")
        .addOption("manual", "使用手动研究设置")
        .setValue(useProfile ? "profile" : "manual")
        .onChange(async value => {
          if (await this.plugin.engineConfigSet("research.use_profile", String(value === "profile"))) this.display();
          else new Notice("研究设置来源保存失败，请重试。", 6000);
        }));
    const profileExists = await this.plugin.reader.exists(this.plugin.reader.path("profile.yaml"));
    const profileActive = useProfile && profileExists;

    const direction = read("direction");
    new Setting(containerEl)
      .setName("研究方向")
      .setDesc(profileActive ? "当前来自 Zotero 画像。切换到手动研究设置后可编辑。" : `当前使用手动设置。${hint}`)
      .addTextArea((area) => {
        area
          .setPlaceholder("分子进化；群体基因组学；…")
          .setValue(typeof direction === "string" ? direction : "")
          .onChange(debounced("direction", () => area.getValue()));
        area.setDisabled(profileActive);
        area.inputEl.rows = 3;
        area.inputEl.addClass("litradar-settings-wide");
      });

    const keywords = read("screening.keywords");
    const keywordText = Array.isArray(keywords) ? keywords.map(String).join(", ") : "";
    new Setting(containerEl)
      .setName("关键词")
      .setDesc(profileActive ? "当前来自 Zotero 画像。切换到手动研究设置后可编辑。" : "逗号分隔，保存后用于文献排序。")
      .addTextArea((area) => {
        area
          .setValue(keywordText)
          .setPlaceholder("EHR foundation models, polygenic risk score")
          .onChange(debounced("screening.keywords", () => {
            const list = area
              .getValue()
              .split(/[,，;；\n]/)
              .map((item) => item.trim())
              .filter((item) => item.length > 0);
            return JSON.stringify(list);
          }));
        area.setDisabled(profileActive);
        area.inputEl.rows = 3;
        area.inputEl.addClass("litradar-settings-wide");
      });

    new Setting(containerEl)
      .setName("日报中文标题")
      .setDesc("通过公开翻译接口添加中文标题并保留英文原题；仅发送文献标题，无需密钥。成功译文会缓存，接口失败时保留原题。")
      .addToggle((toggle) =>
        toggle.setValue(read("translation.enabled") === true)
          .onChange((value) => void save("translation.enabled", value ? "true" : "false")),
      );

    const images = read("images.enabled");
    new Setting(containerEl)
      .setName("置顶配图")
      .setDesc("下载每篇置顶的第一张图/封面到 assets/；失败会自动跳过。")
      .addToggle((toggle) =>
        toggle
          .setValue(images === true)
          .onChange((value) => void save("images.enabled", value ? "true" : "false")),
      );

    const weekday = read("weekly.weekday");
    new Setting(containerEl)
      .setName("周报日")
      .setDesc("这一天的日报跑完后，自动追加一份本周周报。")
      .addDropdown((dropdown) => {
        const days = [
          ["mon", "周一"], ["tue", "周二"], ["wed", "周三"], ["thu", "周四"],
          ["fri", "周五"], ["sat", "周六"], ["sun", "周日"],
        ];
        for (const [value, label] of days) dropdown.addOption(value, label);
        const current = typeof weekday === "number"
          ? days[Math.max(0, Math.min(6, weekday))][0]
          : typeof weekday === "string" && weekday ? weekday : "fri";
        dropdown.setValue(current).onChange((value) => void save("weekly.weekday", value));
      });
  }

  private async renderScheduleSettings(containerEl: HTMLElement): Promise<void> {
    new Setting(containerEl).setName("自动抓取与定时").setHeading();
    new Setting(containerEl)
      .setName("启用 Obsidian 内自动抓取")
      .setDesc("开启后，Obsidian 保持运行时每天按计划检查并抓取；超过计划时间再打开，也会检查补抓。关闭不会取消当前已开始的任务。")
      .addToggle(toggle => toggle.setValue(this.plugin.settings.autoRunEnabled).onChange(async value => {
        try { await this.plugin.updateSettings({autoRunEnabled:value}); }
        catch { toggle.setValue(this.plugin.settings.autoRunEnabled); new Notice("自动抓取开关保存失败"); }
      }));
    new Setting(containerEl).setName("自动补齐与迟到回查")
      .setDesc("每日自动任务回查最近几天，补齐漏抓日期和延迟登记文献；保留原批次内容。可能延长首次运行时间。")
      .addToggle(toggle=>toggle.setValue(this.plugin.settings.autoBackfillEnabled).onChange(value=>void this.plugin.updateSettings({autoBackfillEnabled:value})));
    new Setting(containerEl).setName("回查天数（1–7）")
      .addText(text=>text.setValue(String(this.plugin.settings.backfillDays)).onChange(value=>{const n=Number(value);if(Number.isInteger(n)&&n>=1&&n<=7)void this.plugin.updateSettings({backfillDays:n});}));
    const config = await this.plugin.engineConfig();
    const schedule = config?.schedule as {time?:string;tz?:string;window?:string} | undefined;
    if (!config) {
      containerEl.createEl("p", {text:"无法读取抓取时间，请先检查 Python 解释器和引擎状态。"});
      return;
    }
    let selected = schedule?.time || "09:00";
    const timezone = schedule?.tz || "Asia/Shanghai";
    new Setting(containerEl)
      .setName("每日抓取时间")
      .setDesc(`时区：${timezone}。修改后点击“保存时间”；更改 Obsidian 内计划，已安装的系统任务须另行更新。`)
      .addText(text => {
        text.inputEl.type = "time";
        text.setValue(selected).onChange(value => {selected=value;});
      })
      .addButton(button => button.setButtonText("保存时间").onClick(async () => {
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(selected)) {new Notice("请选择有效的时间（HH:mm）");return;}
        button.setDisabled(true);
        try {
          const ok = await this.plugin.engineConfigSet("schedule.time", selected);
          new Notice(ok ? `每日抓取时间已保存：${selected}（${timezone}）` : "抓取时间保存失败，请检查引擎是否正在运行");
        } finally {button.setDisabled(false);}
      }));
    containerEl.createEl("p", {cls:"setting-item-description", text:"本开关控制 Obsidian 内自动抓取。系统后台定时需单独安装，关闭 Obsidian 后仍要运行时才需要；电脑关机或睡眠期间不会执行抓取。"});
  }

  private renderEngineStatus(containerEl: HTMLElement): void {
    const status = this.plugin.engine;
    const blocker = this.plugin.engineBlocker();
    const tone =
      status.state === "ok"
        ? "litradar-ok"
        : status.state === "checking" || status.state === "unknown"
          ? "litradar-pending"
          : "litradar-bad";

    const box = containerEl.createDiv({ cls: `litradar-engine-box ${tone}` });
    box.createEl("div", {
      cls: "litradar-engine-title",
      text: blocker ? "引擎不可用" : "引擎就绪",
    });
    box.createEl("div", {
      cls: "litradar-engine-detail",
      text: this.plugin.engineSummary(),
    });
    if (blocker) {
      box.createEl("div", { cls: "litradar-engine-blocker", text: blocker });
    }
    if (status.state === "missing") {
      box.createEl("div", {
        cls: "litradar-engine-hint",
        text: "请填写 Python 解释器的绝对路径，然后点击「检测引擎」。",
      });
    }
    if (status.state === "below-min") {
      box.createEl("div", {
        cls: "litradar-engine-hint",
        text: "请执行：pipx upgrade litradar（或 pip install -U litradar），然后重新检测。",
      });
    }
  }
}

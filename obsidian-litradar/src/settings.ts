/**
 * Settings tab. Only configuration lives here.
 *
 * The plugin never handles the API key: the engine reads it from
 * `.litradar/config.yaml` (`llm.api_key` / `llm.api_key_file`) or from the
 * environment variable named by `apiKeyEnv`. Settings persist through Obsidian's
 * own `data.json`; nothing under `.litradar` is ever written.
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

    containerEl.createEl("h2", { text: "Litradar 设置" });
    containerEl.createEl("p", {
      cls: "litradar-settings-intro",
      text:
        "本插件是本地 Python 引擎的薄前端：只读 .litradar 下的 JSON，" +
        "不联网、不加载远程代码、不写 seen.json / store/ / 日报。",
    });

    this.renderEngineStatus(containerEl);

    new Setting(containerEl).setName("引擎").setHeading();

    new Setting(containerEl)
      .setName("Python 解释器路径")
      .setDesc(
        "必填，绝对路径，不依赖 PATH。例如 E:\\conda\\python.exe 或 /usr/local/bin/python3。" +
          " 插件只会用它执行 `-m litradar`。",
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
      .setName("API key 环境变量名（可选）")
      .setDesc(
        "引擎优先读 .litradar/config.yaml 里的 llm.api_key / api_key_file；" +
          "都没有时才读这个环境变量。插件不接触、不保存密钥，只读引擎写出的结果。",
      )
      .addText((text) => {
        text
          .setPlaceholder("LITRADAR_API_KEY")
          .setValue(this.plugin.settings.apiKeyEnv)
          .onChange((value) => {
            void this.plugin.updateSettings({ apiKeyEnv: value.trim() });
          });
        text.inputEl.spellcheck = false;
        text.inputEl.autocomplete = "off";
      });

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
        "插件版本 0.1.0 · 需要 Obsidian 桌面版（child_process 不可用于移动端）。" +
        " 引擎升级请自行执行 `pipx upgrade litradar` 或重新 `pip install -U litradar`。",
    });
    about.createEl("p", {
      text:
        "本插件不写 vault 中的任何文件；设置只保存在插件自己的 data.json 中。" +
        " 报告、store、状态全部由引擎生成。",
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

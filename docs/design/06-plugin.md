# 06 · Obsidian 插件规格

定位：**薄前端**。只做「展示 / 触发 / 配置」，不做业务判断。

## 1. manifest.json

```json
{
  "id": "litradar",
  "name": "Litradar",
  "version": "0.1.0",
  "minAppVersion": "1.5.0",
  "description": "期刊文献雷达：查看每日新上线的期刊文章，一键重抓、重排、入库。",
  "author": "wanglab",
  "isDesktopOnly": true
}
```

`isDesktopOnly: true` 是硬要求（移动端没有 `child_process`）。社区市场会问"为什么需要 child_process"，
在 README 预先回答：本插件是本地 Python 引擎的 GUI，不联网、不加载远程代码。

## 2. 工程与构建

```
obsidian-litradar/
├── manifest.json
├── package.json
├── tsconfig.json
├── esbuild.config.mjs
├── styles.css
└── src/
    ├── main.ts      # 命令、ribbon、视图注册、设置
    ├── bridge.ts    # spawn + JSONL + 超时 + 取消
    ├── settings.ts  # 设置页
    ├── view.ts      # 面板
    ├── store.ts     # 只读 .litradar 下的 JSON
    └── types.ts     # 对齐 03-contracts.md
```

`package.json`：

```json
{
  "scripts": {
    "dev": "node esbuild.config.mjs",
    "build": "tsc -noEmit -skipLibCheck && node esbuild.config.mjs production"
  },
  "devDependencies": {
    "obsidian": "latest", "esbuild": "^0.20.0",
    "typescript": "^5.4.0", "@types/node": "^20.0.0"
  }
}
```

esbuild：`bundle: true`，`external: ["obsidian","electron","@codemirror/*","child_process","fs","path","os"]`，
`format: "cjs"`，`target: "es2018"`，`outfile: "main.js"`。

## 3. 设置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `pythonPath` | string | `E:\conda\python.exe` | **必填**，不依赖 PATH |
| `vaultRoot` | string | 空 = 当前 vault | 引擎工作目录 |
| `storeDir` | string | `.litradar` | 相对 vault 根 |
| `apiKeyEnv` | string | `LITRADAR_API_KEY` | 只存变量名，**不存 key** |
| `autoRefreshSeconds` | number | `30` | 面板轮询间隔；0 = 关 |
| `showLogTail` | boolean | `true` | 失败时展示 stderr 末 20 行 |
| `minEngine` | string | `"2.0.0"` | 版本握手门槛 |

设置页底部放 **「检测引擎」** 按钮：跑 `litradar --version --json`，展示版本与解释器路径。
未通过则所有操作按钮 disabled + 红色横幅。

## 4. 命令与入口

| 命令 id | 名称 | 行为 |
|---|---|---|
| `litradar-open` | 打开面板 | `activateView()` |
| `litradar-fetch` | 重新抓取（今天） | `fetch --jsonl` |
| `litradar-render` | 重新生成日报 | `render --date <today> --llm --jsonl` |
| `litradar-embed` | 重算相关度 | `embed --date <today> --jsonl` |
| `litradar-doctor` | 引擎自检 | `doctor --json` |
| `litradar-open-report` | 打开今日日报 | 打开 `02-Reading/文献雷达/<yyyy>/<MM>/<date>.md` |

Ribbon：`addRibbonIcon("radar", "文献雷达", ...)`。

## 5. 面板（ItemView）

```
div.litradar-view
├── header
│   ├── h4  文献雷达 · 2026-10-29
│   └── div.status   ✅ 已更新 09:00 · 覆盖 partial · 9 篇 / 置顶 2
├── div.toolbar          重抓 / 重生成 / 算相关度 / 打开日报
├── section.coverage     刊 | 状态 | 条数 | 上次成功
├── section.highlights   置顶卡片：标题（点开原文）· 期刊 · 日期 · why
├── section.all          全部条目，可折叠（默认折叠）
├── section.failures     有 blocked/timeout 时出现，红色
└── section.log          折叠的日志尾部
```

数据来源优先级：`runs/<date>/report.md` 的 frontmatter → `store/<date>.json` → `runs/<date>/scores.json`。
**只读**：面板不写任何文件。

### 读隐藏目录

`.litradar` 是点开头目录，`vault.getMarkdownFiles()` 看不到，必须走底层适配器：

```ts
const raw = await this.app.vault.adapter.read(`${storeDir}/state/journal_scans.json`);
const { files } = await this.app.vault.adapter.list(`${storeDir}/store`);
```

## 6. bridge.ts（最关键的一段）

```ts
import { spawn, ChildProcessWithoutNullStreams } from "child_process";

export class Bridge {
  private child?: ChildProcessWithoutNullStreams;

  constructor(private pythonPath: string, private cwd: string) {}

  get busy() { return !!this.child; }

  run(args: string[], timeoutMs: number, onEvent: (e: any) => void) {
    if (this.child) return Promise.reject(new Error("已有任务在运行"));
    return new Promise<{ code: number | null; stderr: string }>((resolve, reject) => {
      const child = spawn(this.pythonPath, ["-m", "litradar", ...args, "--jsonl"], {
        cwd: this.cwd, windowsHide: true,
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
      });
      this.child = child;
      let stderr = ""; let buf = "";
      const timer = setTimeout(() => this.kill(), timeoutMs);

      child.stdout.setEncoding("utf-8");
      child.stdout.on("data", (chunk: string) => {
        buf += chunk;
        let i: number;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
          if (!line) continue;
          try { onEvent(JSON.parse(line)); } catch { /* 非 JSON 行忽略 */ }
        }
      });

      child.stderr.setEncoding("utf-8");
      child.stderr.on("data", (c: string) => { stderr += c; });

      child.on("error", (e) => { clearTimeout(timer); this.child = undefined; reject(e); });
      child.on("close", (code) => { clearTimeout(timer); this.child = undefined; resolve({ code, stderr }); });
    });
  }

  kill() {
    if (!this.child) return;
    if (process.platform === "win32" && this.child.pid) {
      spawn("taskkill", ["/PID", String(this.child.pid), "/T", "/F"], { windowsHide: true });
    } else {
      this.child.kill("SIGTERM");
    }
  }
}
```

要点：

- **必须行缓冲解析**，不能假设一个 chunk 是一行；
- stdout 只吃 JSON；非 JSON 行丢弃；
- 超时用 `taskkill /T` 杀进程树，否则 python 子进程残留；
- `PYTHONUNBUFFERED=1`，否则进度会憋到最后一次性吐出。

## 7. 错误呈现矩阵

| 情况 | 面板 | 通知 |
|---|---|---|
| 退出 0 | 刷新，状态行绿 | 静默 |
| 2 配置错 | 红横幅「配置有误」+ stderr 末 20 行 | Notice 一条 |
| 3 校验失败 | 黄「模型输出被降级/拒收」，链到校验记录 | 静默 |
| 4 全部刊失败 | **红**「今日抓取全部失败」，**不得**显示"无新文章" | Notice 常驻 10s |
| 6 已有任务 | 按钮置灰 +「任务进行中」 | 静默 |
| 7 超时/取消 | 灰「已取消」 | 一条 |
| spawn ENOENT | 红「找不到 python：<path>」链到设置 | 一条 |

## 8. 版本握手

```ts
const { code } = await bridge.run(["--version"], 5000, (e) => { engine = e.engine; });
if (compareVersion(engine, settings.minEngine) < 0) disableAll();
```

不匹配则按钮 disabled + 横幅「引擎 2.0.0 低于插件要求 2.1.0，请执行 `pipx upgrade litradar`」。
**不要**自动升级（会动用户 Python 环境）。

## 9. 绝对禁止

1. 插件里判断相关性、算日期窗、拼报告 —— 一律由引擎产出；
2. 写 `seen.json` / `store/` / 报告文件 —— 只读；
3. 自动 `schtasks /Create`（必须用户自己在管理员终端跑）；
4. API key 存 `data.json` 明文（存环境变量名）；
5. 用 `vault.getMarkdownFiles()` 找 `.litradar`；
6. 缓存"今天有没有数据"超过一个轮询周期（用户可能刚手动重抓）。

## 10. 安装与发布

- 早期：BRAT，release 里带 `manifest.json` + `main.js` + `styles.css`；
- 后期：社区市场，README 写明"本地 Python 依赖 + 数据不出本机"；
- `versions.json` 记录 `minAppVersion` 映射。

## 11. 前端自检清单

- [ ] `.litradar` 不存在时显示「尚未初始化」而非崩溃
- [ ] `pythonPath` 填错报 ENOENT 而非静默失败
- [ ] 抓取中切走视图再切回，进度不丢
- [ ] 连点两次"重抓"，第二次被退出码 6 挡住，不产生两个进程
- [ ] `store/<date>.json` 缺失时显示"今日尚未抓取"
- [ ] 关掉 Obsidian，计划任务仍能出日报（引擎侧验证）

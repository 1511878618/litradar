# Litradar（Obsidian 插件）

Litradar 是一个**本地 Python 引擎**的 Obsidian 薄前端：展示每日新上线的期刊论文、
触发重抓 / 重生成 / 重算相关度，并如实呈现引擎的覆盖情况与失败原因。

> 插件只做「展示 / 触发 / 配置」。日报、`store/`、`seen.json`、相关度、日期窗
> 全部由 `litradar` 引擎产出，插件**只读**，不会写 `.litradar` 下的任何文件。

- 插件版本：0.1.0
- 最低 Obsidian：1.5.0
- 平台：**仅桌面版**（`isDesktopOnly: true`）
- 最低引擎版本：默认 2.0.0（可在设置里调整）

---

## 为什么需要 child_process？

Litradar 的“大脑”是一个本地 Python 包（`litradar`）：它抓取期刊官网、按严格日期窗
筛选、去重、调用模型排序、校验模型输出并生成 Markdown 日报。Obsidian 插件负责把
这些能力带进 vault，因此必须启动本机的 Python 进程并读取它的 `--jsonl` 事件流。

这带来两个直接结论：

1. **仅桌面版**。移动端没有 `child_process`，插件在移动端无法工作，所以
   `manifest.json` 里固定 `isDesktopOnly: true`。
2. **插件不联网、不加载远程代码**。所有网络请求都由用户自己安装的 Python 引擎
   发起；插件只执行 `<pythonPath> -m litradar …` 这一个本地命令，不下载脚本、
   不注入远程 bundle、不做遥测。

插件会执行你在设置里填写的 `pythonPath`。请只填写你信任的解释器绝对路径。

---

## 安装

### 1. 先装引擎（必需）

要求 Python ≥ 3.10，并让引擎在**你指定的解释器**下可导入：

```bash
pipx install litradar
# 或者
/path/to/python -m pip install litradar
```

验证：

```bash
litradar --version --json
# {"engine":"2.0.0","schema":2}
```

记下这个解释器的**绝对路径**，例如：

- Windows：`E:\conda\python.exe`
- macOS / Linux：`/usr/local/bin/python3`

插件不会通过 `PATH` 猜解释器；必须填绝对路径。

### 2. 用 BRAT 安装（推荐，早期版本）

1. 在 Obsidian 中安装并启用 **BRAT**（Beta Reviewers Auto-update Tester）。
2. 打开命令面板 → `BRAT: Add a beta plugin for testing`。
3. 填入本仓库地址（或包含 `manifest.json` 的发布地址）。
4. BRAT 会下载 `manifest.json`、`main.js`、`styles.css` 到
   `<vault>/.obsidian/plugins/litradar/`。
5. 在「设置 → 第三方插件」中启用 **Litradar**。

发布时 release 附件只需包含：`manifest.json`、`main.js`、`styles.css`；
`versions.json` 记录插件版本与 `minAppVersion` 的映射。

### 3. 手动安装

```bash
# 在插件目录构建
cd obsidian-litradar
pnpm install
pnpm run build

# 把三个文件复制到 vault
mkdir -p "<vault>/.obsidian/plugins/litradar"
cp manifest.json main.js styles.css "<vault>/.obsidian/plugins/litradar/"
```

然后在 Obsidian 中启用插件。

### 4. 首次配置

打开「设置 → Litradar」：

1. 填写 **Python 解释器路径**（绝对路径）。
2. 如引擎数据不在当前 vault，填写 **Vault 根目录**（留空 = 当前 vault）。
3. 点击 **「检测引擎」**。成功时会显示引擎版本、schema 与解释器路径。
4. 如果 vault 里还没有 `.litradar`，插件会显示「尚未初始化」。请先运行：

   ```bash
   python -m litradar init --vault "<vault 绝对路径>"
   ```

   插件**不会**替你创建目录（只读原则）。

---

## 使用

- Ribbon 图标（雷达）或命令 `Litradar: 打开面板` 打开右侧面板。
- 面板显示：
  - 今日/最近一次抓取的状态行、覆盖率；
  - 覆盖情况表（期刊 / 栏目 / 状态 / 条数 / 上次成功 / 失败原因）；
  - 「与你最相关」置顶卡片（标题、期刊、日期、why、摘要）；
  - 可折叠的「全部条目」；
  - 失败期刊红色区块（blocked / timeout / error）；
  - 折叠的运行日志末尾 20 行。
- 工具栏：
  - **重抓**：`fetch`（日期窗由引擎配置决定，插件不计算日期）；
  - **重生成**：`render --llm`；
  - **算相关度**：`embed`；
  - **打开日报**：打开引擎生成的日报（优先使用 `report_written` 事件里的路径）；
  - 运行中显示 **取消**（Windows 用 `taskkill /T /F` 终止进程树，其他平台 `SIGTERM`）。

### 命令

| 命令 id | 名称 | 行为 |
|---|---|---|
| `litradar-open` | 打开面板 | 显示面板 |
| `litradar-fetch` | 重新抓取（今天） | `litradar fetch --vault <root> --jsonl` |
| `litradar-render` | 重新生成日报 | `litradar render --llm … --jsonl` |
| `litradar-embed` | 重算相关度 | `litradar embed … --jsonl` |
| `litradar-doctor` | 引擎自检 | `litradar doctor --json` |
| `litradar-open-report` | 打开今日日报 | 打开当前 run 的日报 |

`render` / `embed` 使用引擎产出的 run 信息：单日窗时传 `--date <window.end>`，
区间/周窗时传 `--run-id <id>`，避免插件自行推断日期。

### 设置项

| 键 | 默认 | 说明 |
|---|---|---|
| `pythonPath` | `E:\conda\python.exe` | **必填**，绝对路径，不依赖 PATH |
| `vaultRoot` | 空 | 空 = 当前 vault；引擎工作目录 |
| `storeDir` | `.litradar` | 相对 vault 根 |
| `apiKeyEnv` | `LITRADAR_API_KEY` | **只存变量名**，不存密钥 |
| `autoRefreshSeconds` | `30` | 面板轮询间隔；0 = 关闭 |
| `showLogTail` | `true` | 展示 stderr / run.log 末 20 行 |
| `minEngine` | `2.0.0` | 版本握手门槛 |

---

## 只读与隐私

- **不写 `.litradar`**：插件没有 `adapter.write` / `vault.modify` / `vault.create`；
  设置只保存在插件自己的 `data.json`。
- **不修改 `seen.json`**：只读取，永不写入。
- **不计算**：不做相关性判断、不计算日期窗、不拼 Markdown、不生成报告。
- **不自动升级**：版本不足时只禁用按钮并提示 `pipx upgrade litradar`，绝不改动
  用户的 Python 环境。
- **不自动创建计划任务**：`schtasks /Create` 必须由用户自己执行。
- **不保存 API key**：只保存环境变量名；密钥由引擎从环境变量读取。
- **无遥测、无远程代码**：插件不发起网络请求，不加载远程脚本。
- **本地数据**：`.litradar` 只在本机读写；插件通过 Obsidian 的
  `vault.adapter` 读取隐藏目录（`getMarkdownFiles()` 看不到点目录）。

---

## 版本握手与升级

插件启动后会运行：

```bash
<pythonPath> -m litradar --version --json --vault <vaultRoot>
```

返回 `{"engine":"2.0.0","schema":2}`。若引擎版本低于 `minEngine`：

- 所有操作按钮禁用；
- 面板显示红色横幅：
  「引擎 2.0.0 低于插件要求 2.1.0，请执行 `pipx upgrade litradar`」；
- 插件**不会**自动升级。

升级引擎：

```bash
pipx upgrade litradar
# 或
/path/to/python -m pip install -U litradar
```

然后回到设置页点「检测引擎」。

---

## 错误码与面板表现

| 退出码 | 含义 | 面板 | 通知 |
|---|---|---|---|
| 0 | 成功 | 刷新，状态行绿色 | 静默 |
| 1 | 未预期异常 | 红色横幅 + stderr 末 20 行 | 一条 |
| 2 | 配置错误 | 红色「配置有误」+ stderr | 一条 |
| 3 | `screen.json` 校验失败 | 黄色「模型输出被降级/拒收」，链接到校验记录 | 静默 |
| 4 | 全部期刊抓取失败 | **红色「今日抓取全部失败」**，**不显示“无新文章”** | 常驻 10 秒 |
| 5 | `doctor` 发现问题 | 黄色自检清单 | 一条 |
| 6 | 已有任务在运行 | 按钮置灰 +「任务进行中」 | 静默 |
| 7 | 超时 / 取消 | 灰色「已取消」 | 一条 |
| spawn ENOENT | 找不到解释器 | 红色「找不到 python：<path>」+ 打开设置 | 一条 |

> 只有「所有刊 `status=ok` 且 `items_seen=0`」这一种组合，面板才会说
> “所有期刊本轮均无新条目”。退出码 4 永远不会被渲染成“没有新文章”。

---

## 构建

需要 Node.js 18+ 与 pnpm：

```bash
cd obsidian-litradar
pnpm install
pnpm run build
```

`build` 执行：

1. `tsc -noEmit -skipLibCheck`（类型检查，不产出文件）
2. `node esbuild.config.mjs production`（CJS bundle → `main.js`）

开发模式（watch）：

```bash
pnpm run dev
```

构建产物：`main.js`（CJS，external: `obsidian` / `electron` / `child_process` …）。
`main.js` 不进入 git（见仓库根 `.gitignore`）。

---

## 故障排查

| 现象 | 处理 |
|---|---|
| 红色「找不到 python」 | 设置里的路径写错；填写**绝对路径**，不要写 `python` |
| 「引擎 2.0.0 低于插件要求」 | `pipx upgrade litradar`，不要指望插件自动升级 |
| 「尚未初始化」 | 先在终端运行 `python -m litradar init --vault "<vault>"` |
| 「今日尚未抓取」 | 点「重抓」或运行 `litradar fetch`；面板每个轮询周期都会重读 `store/` |
| 覆盖不完整 / 某刊被拦截 | 看覆盖表的 reason；这是出版社反爬，不是插件故障 |
| 面板一直显示旧数据 | 检查 `autoRefreshSeconds` 是否为 0；或点「重抓」 |
| 找不到日报 | 先「重生成」；日报路径默认 `02-Reading/文献雷达/<yyyy>/<MM>/<date>.md` |

---

## 开发约束（给贡献者）

- 不要在插件里判断相关性、算日期窗、拼报告；
- 不要写 `seen.json` / `store/` / 报告文件；
- 不要用 `vault.getMarkdownFiles()` 找 `.litradar`（点目录不可见），用
  `this.app.vault.adapter`；
- 不要缓存“今天有没有数据”超过一个轮询周期；
- stdout 只解析 JSONL，人类日志走 stderr；行缓冲，不能假设 chunk 边界；
- 取消/超时必须杀进程树（Windows `taskkill /T /F`，其他 `SIGTERM`）；
- 不自动升级引擎、不自动创建计划任务、不保存 API key。

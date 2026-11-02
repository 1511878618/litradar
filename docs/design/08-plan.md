# 08 · 里程碑与验收

> 顺序不可颠倒：**先用 CLI 把日报跑稳，再加插件**。反过来会得到"界面漂亮但没有数据"。

## M0 · 现状盘点（0.5 天）

```bat
cd D:\AI小工具\litradar
E:\conda\python.exe tests\run_tests.py       :: 期望 TOTAL 37 passed
E:\conda\python.exe -m litradar --version
E:\conda\python.exe -m litradar doctor --vault "D:\my_brain\XTF_kgb"
```

验收：37 用例全绿，doctor 三刊全通。

## M1 · 引擎 v2（3–4 天）

| # | 任务 | 验收 |
|---|---|---|
| 1.1 | `--jsonl` 全命令支持，事件按 `02-engine.md` §3 | 任意命令加 `--jsonl`，stdout 每行可 `json.loads` |
| 1.2 | `fetch` / `render` / `run` 拆分，`--date` 语义 | `--date 2026-10-20` 能补跑历史日 |
| 1.3 | `journals/*.yaml` 拆分加载（兼容单文件 config.yaml） | 加一刊只改 yaml，不改代码 |
| 1.4 | `custom` 适配器加载（`importlib`） | 假 custom 模块能跑；抛异常 → 该刊 `error`，其余继续 |
| 1.5 | 文件锁 + 退出码 6 | 两个 `run` 并发，第二个立即退 6 |
| 1.6 | `journals test NAME` | 输出前 3 条的 title/date/doi |
| 1.7 | `--version --json` | `{"engine":"2.0.0","schema":2}` |
| 1.8 | LLM screening 接真实 API（可 mock） | 无 key 降级 WARN；有 key 产出合法 screen.json |
| 1.9 | 回归测试补齐（幂等/锁/jsonl/custom） | `run_tests.py` ≥ 50 passed |

**DoD**：`litradar run --jsonl` 无人值守产出日报与索引，失败三态齐全。

## M2 · 定时与可运维（1–2 天）

| # | 任务 | 验收 |
|---|---|---|
| 2.1 | `schedule print/install/uninstall` | `install` 打印命令 + 写 `run-daily.cmd`；**不自动创建任务** |
| 2.2 | `--catch-up --max-days 3` | 删掉两天 store，跑一次补两天的日报 |
| 2.3 | 日志滚动 7 天 + key 脱敏 | 日志搜不到 `sk-` 明文 |
| 2.4 | `doctor --json` | 插件可直接渲染 |

**DoD**：关闭 Obsidian、锁屏，9 点自动跑；开机晚了能补跑。

## M3 · Obsidian 插件（4–5 天）

| # | 任务 | 验收 |
|---|---|---|
| 3.1 | 骨架（manifest/构建/设置页） | BRAT 装上，设置可保存 |
| 3.2 | `bridge.ts`（spawn + JSONL + 超时 + 取消） | 重抓时能看到逐刊进度 |
| 3.3 | 面板（覆盖率表/置顶/全量折叠/失败红字） | 三态正确呈现 |
| 3.4 | 版本握手 + 按钮 disabled 逻辑 | 调高 `minEngine` 后按钮全禁用并提示 |
| 3.5 | 错误矩阵（`06-plugin.md` §7） | 0/2/3/4/6/7/ENOENT 逐个人为制造并验证 |

**DoD**：Obsidian 里能看到今天抓了什么、哪些刊失败、置顶哪几篇；按钮能跑完整流程。

## M4 · Zotero 与 embedding（3–4 天，可选）

| # | 任务 | 验收 |
|---|---|---|
| 4.1 | `zotero collections` / `push` | 12 篇入库，重复 DOI 跳过 |
| 4.2 | profile 三级回退（zotero → tags → text） | 关掉 Zotero 不报错 |
| 4.3 | `embed` + `scores.json` + 融合排序 | 置顶顺序随分数变化 |
| 4.4 | 降级（无依赖/无模型/维度不符） | 日报照常，面板标"相关度不可用" |

**DoD**：embedding 是可拔插增强，拔掉后一切照常。

## M5 · 反爬（可选）

OUP / PNAS / Elsevier 类站点 403。按成本排：

1. `crossref` 兜底（已可用，标 metadata-only）——**先这样**；
2. 复用浏览器 cookie —— 中等成本，脆弱；
3. Playwright 无头浏览器 —— 重但通用，作为 `adapter: browser` 单独 extras。

不到万不得已不要上 3。

---

## 风险表

| 风险 | 影响 | 对策 |
|---|---|---|
| 出版社改版 | 静默漏文 | `journals test` + `doctor`；条目数骤降告警 |
| Cloudflare 加严 | 多刊 blocked | 明确标注 + crossref 兜底，不假装成功 |
| Python 环境漂移 | 引擎跑不起来 | doctor 首行打印解释器与依赖版本；版本握手 |
| 插件审核 | 上不了市场 | 先 BRAT；README 讲清本地依赖 |
| LLM 输出不合规 | 置顶错乱 | validate 降级 + 「校验记录」 |
| embedding 2GB 依赖 | 劝退 | 默认关，extras 安装，失败不阻塞 |
| 用户改 `seen.json` | 永久漏报 | 文件头写 DO NOT EDIT；插件只读 |

## 交付物

- [ ] `litradar` 包（`pipx install` 可用，含 `[embed]` extras）
- [ ] `journals/` 示例（≥3 本实测：Nature Genetics / PLOS Genetics / MBE）
- [ ] 文档（本规格 + 用户手册 + 新增期刊 SOP）
- [ ] `obsidian-litradar` 插件（BRAT 可装）
- [ ] `schtasks` 安装 + 补跑脚本
- [ ] CI：`run_tests.py` + `tsc --noEmit` + `ruff`

## 未决（先按默认实现）

| # | 问题 | 默认 |
|---|---|---|
| 1 | LLM 用哪家 | 任何 OpenAI 兼容端点，默认 `deepseek-chat` |
| 2 | 是否推送（微信/邮件） | 不做 |
| 3 | 日报 vs 周汇总 | 每日（`--kind weekly` 保留能力） |
| 4 | 是否接 arXiv | 不接 |

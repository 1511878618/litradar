# 09 · 本机环境事实与已知坑

> 接手必读。这里的失败模式很反直觉，照常规做法会连栽几次。

## 1. 机器与路径

| 项 | 值 |
|---|---|
| 主机 / 用户 | `DESKTOP-D5TL5N2` / `DELL` |
| 系统 | Windows + Obsidian 桌面版 |
| 工作区 | `D:\AI小工具`（沙盒只允许写这里，其他路径需授权） |
| 参考实现 | `D:\AI小工具\litradar\`（Python 引擎，37 测试通过） |
| 本规格 | `D:\AI小工具\litradar-v2\` |
| 另一条产品线 | `D:\AI小工具\biorxiv-radar\`（**另一个 agent 做预印本，不要动**） |
| 主 vault | `D:\my_brain\XTF_kgb` |
| 其他 vault | `D:\wanglab_jobs`、`D:\XTF_kgb`、`D:\OneDrive\工作\wanglab\科研阅读练习\review阅读练习` |
| Obsidian 配置 | `C:\Users\DELL\AppData\Roaming\obsidian\obsidian.json` |
| 已部署引擎副本 | `D:\my_brain\XTF_kgb\.litradar\` |
| 已生成日报 | `D:\my_brain\XTF_kgb\02-Reading\文献雷达\2026\10\2026-10-29.md` |
| 已装 DSH 技能 | `C:\Users\DELL\.dsh\skills\literature-monitor\SKILL.md` |

vault 结构（跟着现有分类走，别建平行体系）：

```
01-Diary/  02-Reading/  03-Projects/  04-笔记/
02-Reading/4-betternotes/     # 已有 @ 开头笔记，tags: unread 约定
```

## 2. Python：两个解释器，别用错

| 解释器 | 版本 | 已有依赖 | 用途 |
|---|---|---|---|
| `E:\conda\python.exe` | 3.12.7 | ✅ httpx / bs4 / lxml / pyyaml / pytest | **主力** |
| `C:\Users\DELL\.dsh\dsh-runtimes\...\python\python.exe` | 3.12.14 | 仅 lxml | DSH 内置，缺 yaml/bs4/httpx，**别当运行时** |

```bat
E:\conda\python.exe -c "import httpx,bs4,lxml,yaml;print('deps ok')"
```

## 3. 已知坑

### 3.1 `venv` / `ensurepip` 必挂

症状：`python -m venv .venv` 报 `ensurepip ... exit status 1`，
根因是 `TemporaryDirectory` 清理时 `chmod` 被拒（`WinError 5`）。

- **不要建 venv**，直接用 `E:\conda\python.exe`；
- 需要隔离就用 `pipx install --python E:\conda\python.exe litradar`，由用户在**普通 PowerShell** 里跑。

### 3.2 `tempfile.mkdtemp` 建出的目录不能建子目录

实测：`mkdtemp()` 返回的目录里 `os.mkdir()` 直接 `PermissionError WinError 5`；同位置 `os.makedirs()` 正常。

- 代码里**禁止**用 `mkdtemp` 建目录，用 `os.makedirs(unique_name)`；
- 原子写不要 `mkstemp`，用同目录 `.tmp` + `os.replace`（v1 已改）；
- `pytest` 的 `tmpdir` 插件会踩这个，所以 v1 自带 `tests/run_tests.py`，**别改回 pytest**。

### 3.3 沙盒创建过的目录可能删不掉

症状：`Remove-Item -Recurse` 报拒绝访问，`Get-ChildItem` 也进不去。修法（提权）：

```powershell
takeown /F "<目录>" /R /D Y
icacls "<目录>" /reset /T /C
Remove-Item "<目录>" -Recurse -Force
```

### 3.4 工作区 ACL

`D:\AI小工具` 曾因只继承 `Authenticated Users: Modify`（无 `FullControl`）导致
`SetNamedSecurityInfoW failed (Win32 5): grantWrite(...)`。已修：

```powershell
icacls "D:\AI小工具" /grant "DESKTOP-D5TL5N2\DELL:(OI)(CI)F" /T
```

新项目放 `C:\Users\DELL\...` 下就永不遇到。

### 3.5 写 vault 需授权

沙盒只覆盖 `D:\AI小工具`。写 `D:\my_brain\XTF_kgb` 需要一次提权，或以 vault 为工作目录新开会话。
**不要反复重试被拒的写操作。**

### 3.6 中文文件别用 PowerShell 改写

PS 5.1 的 `Get-Content` 默认按 ANSI(GBK) 读、`Set-Content -Encoding utf8` 会加 BOM，
UTF-8 中文文档经它一过就变成不可逆乱码（本次已踩过一次，10 个文件重写）。

- 改中文文档用 UTF-8 写入工具，或 `[System.IO.File]::WriteAllText($p,$s,[Text.UTF8Encoding]::new($false))`；
- 校验：文件头不应出现 `EF BB BF`。

## 4. 网络可达性（2026-10 实测）

| 目标 | 结果 | 说明 |
|---|---|---|
| `www.nature.com/ng.rss` | ✅ 200 | RDF，16KB，含 DOI 与 publicationDate |
| `journals.plos.org/plosgenetics/feed/atom` | ✅ 200 | Atom，105KB |
| `api.crossref.org/works` | ✅ 200 | 建议带 `mailto` |
| `pypi.org/simple/` | ✅ 200 | pip 可联网 |
| `academic.oup.com/mbe/advance-articles` | ⛔ 403 | Cloudflare |
| `www.pnas.org/toc/pnas/0/0` | ⛔ 403 | 同上 |
| `genome.cshlp.org` | ⚠ 302 循环 | 需 cookie 链 |
| `genomebiology.biomedcentral.com/articles` | ✅ 200 | HTML 340KB |

请求至少带浏览器 UA + `Accept-Language`，否则更多站点 403。

## 5. 试跑集（三本刊，已验证）

| 期刊 | adapter | 标识 | 备注 |
|---|---|---|---|
| Nature Genetics | `rss` | `https://www.nature.com/ng.rss` | 摘要需去样板与 HTML |
| PLOS Genetics | `rss` | `https://journals.plos.org/plosgenetics/feed/atom` | 摘要完整 |
| Molecular Biology and Evolution | `crossref` | ISSN `0737-4038` | 只能 metadata-only |

## 6. 跑测试与构建

```bat
cd D:\AI小工具\litradar
E:\conda\python.exe tests\run_tests.py          :: 期望 TOTAL 37 passed
E:\conda\python.exe -m litradar --version
E:\conda\python.exe -m litradar doctor --vault "D:\my_brain\XTF_kgb"
```

插件构建（Node 由 DSH 运行时提供，找不到就装 Node LTS）：

```bat
node --version
pnpm --version
cd obsidian-litradar
pnpm install
pnpm run build      :: 产出 main.js
```

## 7. 与现有部署的关系（别打架）

| 现状 | 处理 |
|---|---|
| DSH 内有每日 09:00 提醒任务 | 切到 Windows 计划任务后**删掉**，避免一天跑两次 |
| `.litradar/` 目录名 | v2 沿用，兼容既有 state 与日报 |
| `02-Reading/文献雷达/` 有 `2026-W43.md` 周报残留 | 可留可删，日报从 `2026-10-29.md` 起算 |
| `seen.json` 已有 2 条 | 保留，避免重复报告 |

## 8. 交接检查单

1. 读 `README.md` → `01-architecture.md` → `03-contracts.md` → `08-plan.md`；
2. 跑 `tests/run_tests.py`，确认环境可用；
3. 跑 `litradar run --vault "D:\my_brain\XTF_kgb" --no-llm`，确认能出日报；
4. 从 M1 的 1.1（`--jsonl`）开始，**不要先写插件**；
5. 每个里程碑用对应 DoD 自检后再进下一个。

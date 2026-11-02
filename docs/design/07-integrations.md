# 07 · 集成：定时 / LLM / Zotero / embedding

## 1. 定时（Windows 任务计划）

### 1.1 为什么必须走系统计划任务

Obsidian 插件没有后台常驻能力：关机、关掉 Obsidian、托盘退出，插件都不跑。
**日报可靠性完全由计划任务保证。**

### 1.2 安装器

`schtasks /TR` 不能设工作目录，统一用 `.cmd` 包装：

```bat
@echo off
rem %VAULT%\.litradar\run-daily.cmd
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "D:\my_brain\XTF_kgb"
"E:\conda\python.exe" -m litradar run --vault "D:\my_brain\XTF_kgb" --window yesterday --llm --jsonl >> ".litradar\logs\daily.log" 2>&1
exit /b %ERRORLEVEL%
```

```bat
schtasks /Create /TN "Litradar Daily" /SC DAILY /ST 09:00 /F ^
  /TR "\"D:\my_brain\XTF_kgb\.litradar\run-daily.cmd\""
```

卸载：`schtasks /Delete /TN "Litradar Daily" /F`。

### 1.3 错过补跑

任务计划默认**不补跑**（9 点关机、10 点开机 → 今天没跑）。二选一：

**A. 启动触发器**（零代码）

```bat
schtasks /Create /TN "Litradar Catchup" /SC ONLOGON /F ^
  /TR "\"D:\my_brain\XTF_kgb\.litradar\catchup.cmd\""
```

`catchup.cmd`：检查 `<today>.json` 是否存在，不存在就跑 `--window yesterday`（先 `timeout /t 60` 等网络，失败重试 3 次）。

**B. 引擎内置**：`run --catch-up --max-days 3`，检查最近 3 天哪些日期没有 `store/*.json` 并逐日补跑。
必须限天数，防止一次补 30 天。

### 1.4 电源

笔记本默认"电池供电不运行"会漏跑。安装时提示用户取消
任务属性 → 条件 → "只有在计算机使用交流电源时才启动此任务"。

### 1.5 插件侧

只提供 **「复制安装命令」** 按钮（把两条 `schtasks` 放进剪贴板），**不执行**。
再加「检查计划任务」：`schtasks /Query /TN "Litradar Daily" /FO LIST`，把结果展示出来。

## 2. LLM（Python 端）

### 2.1 调用

OpenAI 兼容端点：

```python
resp = client.post("/chat/completions", json={
    "model": cfg.llm.model,
    "temperature": 0.2,
    "response_format": {"type": "json_object"},
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ],
})
```

`payload` 只带必要字段，控制 token：

```json
{"direction": "…", "journals": ["…"], "papers": [
  {"id": "sha1:…", "journal": "…", "title": "…", "abstract": "…（≤1200 字符）"}
]}
```

### 2.2 提示词要点

- **逐条回答**，`results` 长度必须等于 `papers` 长度（校验层核对）；
- **禁止改写摘要**：`verbatim` 要求"原样复制"；
- **只输出 JSON**，schema 写在 system；
- `why` 限 40 字中文；
- 不要"总体趋势"（最容易编）。

### 2.3 成本与限流

- 40 篇 ×（标题 + 1200 字符摘要）≈ 25k input + 3k output；一轮约 ¥0.05–0.1；
- `max_items: 60` 上限保护；`max_retries: 2`，只重试本次请求；
- 429/5xx → 退避 2s/4s；超时 → 直接降级，不阻塞日报。

### 2.4 key 管理

- 只从环境变量读（`llm.api_key_env` 指定变量名）；
- 日志脱敏：`sk-[A-Za-z0-9]{10,}` → `sk-***`；
- 缺 key 时：WARN + 自动降级关键词，**不报错**。

## 3. Zotero

### 3.1 读（profile / 关联）

Zotero 7 需在设置里开启 "Allow other applications on this computer to communicate"。

| 用途 | 端点 |
|---|---|
| 列收藏夹 | `GET /api/users/0/collections` |
| 收藏夹条目 | `GET /api/users/0/collections/<key>/items?format=json&limit=100` |
| 按 DOI 查 | `GET /api/users/0/items?q=<doi>&qmode=everything` |

字段：`data.title` / `data.DOI` / `data.abstractNote`。

回退链：本地 API → zotlit 同步的本地 JSON → Better BibTeX 导出 → 放弃并提示。

### 3.2 写（一键入库）

`POST http://127.0.0.1:23119/connector/saveItems`：

```json
{"sessionID":"litradar-2026-10-29","uri":"https://www.nature.com/",
 "items":[{"itemType":"journalArticle","title":"…","DOI":"10.1038/…",
           "url":"…","publicationTitle":"Nature Genetics","date":"2026-10-28",
           "abstractNote":"…","creators":[]}]}
```

- Zotero 会弹保存确认，提前告知用户；
- 只传官方字段；集合归属入库后再用本地 API 补；
- 入库前按 DOI 查重，已存在则 `skipped`。

### 3.3 命令

```
litradar zotero push --date 2026-10-29 [--only-relevant] [--collection KEY]
→ {"pushed": 12, "skipped": 3, "failed": 0}
```

`--jsonl` 时逐条输出事件供插件显示进度。

## 4. Embedding（可选增强）

### 4.1 profile 来源

| 来源 | 写法 | 质量 |
|---|---|---|
| Zotero 收藏夹 | `zotero:collection/进化基因组学` | 最高 |
| vault 标签 | `tags:#litradar/interest` | 中 |
| 手写方向 | `text:进化基因组学；正选择` | 低（零配置） |

每条的「标题+摘要」各自 embedding → 归一化 → **取质心**。条目 > 200 时随机采样 200。

### 4.2 计算

```python
model = SentenceTransformer(cfg.embedding.model)      # 首次加载 10–20s
vecs = model.encode(texts, normalize_embeddings=True)  # 每篇 ~20ms
score = float(profile_vec @ paper_vec)                 # 余弦
```

- 缓存 `vectors/<sha1(doi)>.npy`，按 `model` 名分目录（换模型自动失效）；
- 只算新 DOI；删缓存目录即全量重算；
- 输出 `scores.json`（见 `03-contracts.md` §8）。

### 4.3 融合

```
final = 0.7 * llm_relevance + 0.3 * embed_score
```

兜底链：两者都无 → 关键词命中；只有 embedding → 按分数取 top_k；只有 LLM → 按 relevance。

### 4.4 失败降级（必须实现）

| 情况 | 行为 |
|---|---|
| `import sentence_transformers` 失败 | WARN，面板"相关度不可用"，日报照常 |
| 模型下载失败（离线） | 报错一次，7 天内不再尝试 |
| 计算中途异常 | 保留上次 `scores.json`，标 `stale: true` |
| 向量维度与缓存不符 | 清该模型目录重算 |

**embedding 永不允许阻塞日报。**

### 4.5 依赖

`pip install "litradar[embed]"`（带 sentence-transformers + torch）。没装 extras 时 `embed` 返回「未安装 embed 依赖」，退出码 5，不要崩栈。

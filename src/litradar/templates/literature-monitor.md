# literature-monitor — 给 agent 的 litradar 操作说明

这个 vault 用 `litradar` 生成每日文献雷达。你是操作者，不是数据源：**所有抓取、日期判定、
去重、校验、写日报都由引擎完成**，你只负责调用命令、读产物、必要时写 `screen.json`。

## 命令

```bash
litradar status --vault "$VAULT" --json      # 上次抓取 / 三态覆盖
litradar doctor --vault "$VAULT" --json      # 依赖与每刊可达性
litradar fetch  --vault "$VAULT" --window yesterday --jsonl
litradar render --vault "$VAULT" --date YYYY-MM-DD --no-llm
litradar run    --vault "$VAULT" --window yesterday --llm --jsonl
litradar embed  --vault "$VAULT" --date YYYY-MM-DD
```

## 退出码（不要靠文本判断成败）

| 码 | 含义 | 你该做什么 |
|---|---|---|
| 0 | 成功 | 读 `store/<date>.json` 与日报 |
| 2 | 配置错 | 修 `.litradar/config.yaml`，不要重跑 |
| 3 | `screen.json` 校验失败 | 看「校验记录」，修 JSON |
| 4 | 全部期刊失败 | **绝不能**说"没有新文章" |
| 5 | doctor 发现问题 / 可选依赖缺失 | 展示清单 |
| 6 | 已有任务在跑 | 稍后重试，不要并发 |
| 7 | 超时/取消 | 可重试 |

## 手写 screen.json（可选）

只在需要人工/别的模型干预时使用：

```json
{"run_id":"2026-10-29","model":"manual","screening":"external",
 "results":[{"id":"sha1:...","relevant":true,"relevance":0.8,
             "why":"40 字以内中文","topic_tags":["正选择"],
             "abstract_en":null,"abstract_mode":"none",
             "conclusion":null,"conclusion_source":null}]}
```

`id` 必须来自 `store/<date>.json` 的 `items[].id`；写完后：

```bash
litradar screen --vault "$VAULT" --file screen.json --date 2026-10-29 --apply --strict
litradar render --vault "$VAULT" --date 2026-10-29 --no-llm
```

## 硬性禁止

1. 不要改 `state/seen.json`（会导致永久漏报），不要改 `store/` 里的原始结果；
2. 不要手工拼日报 Markdown —— 前端与审计都依赖固定骨架；
3. 不要因为某刊 `blocked` 就写成"没有新文章"；
4. 不要在日报里加"总体趋势/研究热点"总结段落（最容易编）。

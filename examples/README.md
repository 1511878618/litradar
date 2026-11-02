# 示例期刊配置

把需要的文件复制到 `<vault>/.litradar/journals/`，然后：

```bash
litradar journals test "<期刊名>"
```

| 文件 | 演示什么 |
|---|---|
| `the-plant-cell.yaml` | HTML 栏目页 + CSS 选择器 + crossref 降级 |
| `cell.yaml` + `cell_custom.py` | `custom` 适配器（自己的 Python 模块） |
| `_template.yaml` | 所有可选字段的带注释模板（排序/标题层级/配图/过滤） |
| `../llm-deepseek-flash.yaml` | DeepSeek flash 的 llm + screening 配置片段 |
| `biorxiv-genomics.yaml` | 预印本服务器 + `categories` 收窄分类 |
| `manual.yaml` | 完全不联网的人工兜底 |

这些站点随时会改版，示例的意义是**展示写法**，不是保证可用。
`the-plant-cell.yaml` 指向的 OUP 站点对自动化请求返回 403 —— 正是它演示
`fallback: [crossref]` 的原因：拿不到官网就标「仅元数据」，而不是假装成功。

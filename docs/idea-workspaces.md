# 研究专题与 llm-for-zotero

引擎 2.5.0 / Obsidian 0.7.0 / Zotero Bridge 0.3.0。

Litradar 负责收集文献、组织专题、记录研究问题和个人批注；[llm-for-zotero](https://yilewang.github.io/llm-for-zotero/zh/) 负责阅读、对话与 Markdown 保存。

## 使用流程

1. 打开 Zotero 10，启用本地 API（设置 → 高级 → 允许其他应用程序通信），安装本仓库的 Zotero Bridge 0.3.0，并启用 llm-for-zotero **3.9.17**。
2. 在 Obsidian 运行「Litradar: 打开研究专题」，从雷达或 Zotero 收藏夹加入论文，整理角色与个人批注。
3. 选择 1–30 篇，点击「在 Zotero 中讨论所选文献」。首次写入时在 Zotero 授权。已有条目直接复用；未入库文献先创建 Zotero 条目，不创建每次任务收藏夹。
4. llm-for-zotero 打开新对话，将有本地全文附件的条目作为上下文。Litradar 显示每篇的附加结果；没有附件的条目需先在 Zotero 补充 PDF 或其支持的其他附件。
5. 在 Zotero 直接提问，例如「比较这些文章的背景、研究路线、核心证据与尚未解决的问题」。专题问题与批注保留在 Litradar，本版本不自动发送任何消息。
6. 使用 llm-for-zotero 自己的 Markdown 保存功能，手动将文件笔记/输出目录设为 vault 下的文件夹，例如 `Tingfeng_KB/02-Reading/研究专题`。保存后 Obsidian 按普通 Markdown 笔记展示，无需返回 Litradar 收回结果。

## 附加成功的含义

桥接使用 llm-for-zotero 原生添加上下文命令，核对新会话的文献标签和库身份；仅打开窗口不算成功。附加成功表示文献已成为上下文候选，不表示模型已读取全部全文或完成归纳。实际阅读与引用由 llm-for-zotero 管理。

单次最多 30 篇。缺少本地附件的条目不会伪装为已加入；未知附件格式或会话未就绪时显示未确认。部分成功时保留成功会话，并列出未成功的文献。如果通信中断，请先检查 Zotero 会话再重试，避免重复开会话。

此入口是针对 3.9.17 原生菜单和上下文界面的版本适配器，不是上游公开的通用 LLM API。检测到其他版本或结构变化会停止并提示更新适配。它不读取模型密钥、不发送消息、不抓取回答、不更改 Markdown 输出路径。

## 兼容与数据

旧版专题、批注、归纳和协作任务文件原样保留。有归纳结果时可在「旧版归纳记录」打开。旧版 CLI `handoff/sync/receive` 保留兼容，面板不再提供这些步骤；独立 CLI 的旧模型命令同样保留，但面板不会调用。

Obsidian 日报和重渲染使用 `--no-llm`，科研画像使用本地关键词，中文标题翻译仍可单独启用。

专题资料位于 `.litradar/research/ideas` 和 `papers`。新入口只更新文献的 Zotero 身份，不生成分析任务或结果副本。跨专题的同一论文会复用 Zotero 条目。

```sh
litradar ideas discuss --vault /path/to/vault --idea-id TOPIC_ID --paper-ids-json '["PAPER_ID"]'
```

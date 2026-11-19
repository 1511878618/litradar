# Litradar Zotero Bridge 0.3.1

将 Litradar 与 Zotero **10.0.x**、Reading List **1.7.1** 连通：共享阅读状态，并调用 Zotero 自己的全文查找队列。

## 升级与安装

在 Zotero「工具 → 插件 → 齿轮 → 从文件安装插件」选择 [litradar-pdf-bridge-0.3.1.xpi](litradar-pdf-bridge-0.3.1.xpi)。沿用旧 PDF Bridge 的插件 ID，直接覆盖升级，不需要同时安装两份；文件名保留 `pdf-bridge` 前缀以延续分发方式。

启用 Reading List 和 Zotero「设置 → 高级 → 允许其他应用通信」。首次写入或下载时按 Zotero 弹窗授权。然后重新启用 Obsidian 的 Litradar。完整步骤见 [联动安装教程](../docs/zotero-setup.md)。

## 连接诊断与本地附件

0.3.1 新增只读 GET `/api/users/0/litradar/health`，分别返回插件安装、启用、初始化及版本信息；GET `/api/users/0/litradar/files?key=ITEMKEY1` 检查附件是否存在于本机、默认全文能否用于讨论及 Zotero 查找队列状态。均继承原生本地 API 通信开关和个人文库校验，不读取或返回文件内容、路径、模型配置及密钥。

Obsidian 的“连接状态”与 `litradar zotero diagnose --json` 使用这些信息显示处理步骤。旧桥接降级为端点探测，不会将未确认的安装状态报告为未安装。此版本没有放宽 llm-for-zotero 的兼容版本限制。

## 阅读状态接口

- GET `/api/users/0/litradar/reading` 返回 Reading List 当前状态选项及有状态的个人文库条目。
- POST 同一路径调用 Reading List 的 `setItemReadStatus(item, status, false)`，在事务中等待保存；清除状态只移除其 `Read_Status`、`Read_Status_Date` 字段及对应状态标签。
- 状态使用 Reading List 的真实名称，支持自定义状态。Zotero 中修改后，Litradar 下次刷新读取；不是实时推送。
- 保留其他 Extra 内容和项目标签，遵循 Reading List 的标签同步偏好，并校验条目版本防止覆盖并发修改。
- 旧 Litradar 标签和本地清单由用户显式迁移。已有 Reading List 状态优先保留；成功后清理旧记录，失败时保留并可重试。

## PDF

POST `/api/users/0/litradar/pdf` 调用 `Zotero.Attachments.addAvailableFiles()`。下载由 Zotero 管理，进度与结果在其原生窗口查看；任务受理不表示已获得全文。

## 专题上下文

启用 llm-for-zotero **3.9.17** 后，GET `/api/users/0/litradar/llm-context` 检查适配器是否可用。POST 提交 `request_id`（32 位小写十六进制）与 `keys`（1–30 个不重复的个人文库条目 key），调用其原生添加上下文命令。以相同请求标识轮询 GET `?request_id=...` 获取逐篇回执；重复 POST 同一请求不会重新开会话。

适配器确认新对话的库身份和文献标签；缺少本地附件的条目会跳过。它不发送消息、不调用模型、不读取密钥、不保存回答。用户在 Zotero 中聊天，手动将 llm-for-zotero 的 Markdown 输出目录设到 vault 下。仅适配已验证的 3.9.17，其他版本须重新验证。

已在 Zotero 10.0.7 / llm-for-zotero 3.9.17 使用两篇已有 PDF 的论文完成真实附加，回执见 [verification-context-0.3.0.json](verification-context-0.3.0.json)。这项验证未发送模型请求。

## 授权与兼容

所有端点都继承 Zotero 原生本地 API 的通信开关、Server ID 和写入授权验证；GET 状态读取不需要写入授权。仅处理个人文库中的正常文献条目。

0.2.0 在隔离 Zotero 10.0.7 和真实 Reading List 1.7.1 中进行原生安装、双向状态修改、自定义状态、迁移、版本冲突和清除状态验证。记录见 [verification-reading-list-0.2.0.json](verification-reading-list-0.2.0.json)。尚未验证其他 Reading List 版本；其方法不可用时接口会明确报错，不退回另一套标签状态。

## 构建和验证

仓库根目录：

```bash
python3 zotero-litradar/build.py
python3 zotero-litradar/tests/native_reading_list.py --reading-list /path/to/reading-list.xpi
```

原生测试会启动独立 Zotero、使用临时空白文库，并在测试配置内模拟“始终允许”；不会对日常文库授权或写入。

## 更新策略

本机定制版采用手动安装更新。Zotero 加载器要求非空 HTTPS 更新地址，使用保留地址 `https://litradar.invalid/updates.json`，无自动更新服务，后台更新检查会无法解析该地址。公开发布时须替换为真实 HTTPS 更新清单；不关闭 Zotero 安全检查。

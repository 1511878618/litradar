# Zotero 联动安装教程

本教程适用于 Litradar 的 Zotero 阅读与 PDF 工作流。仅抓取文献、生成日报时，不需要安装 Zotero 或以下扩展。

## 组件与用途

| 组件 | 安装位置 | 何时需要 | 用途与兼容范围 |
|---|---|---|---|
| Zotero 10 桌面版 | 电脑 | 使用当前阅读状态写入、入库或 PDF 联动时必需 | 提供本地 API，管理文献与附件；Litradar 已在 10.0.7 验证读取 |
| [Zotero Reading List](https://github.com/Dominic-DallOsto/zotero-reading-list) | Zotero → 工具 → 插件 | 使用 Reading List 状态联动时必需；不影响文献抓取和日报 | 提供新增、待读、阅读中、已读、不读等状态，支持状态列、批量修改、自定义状态、快捷键和打开附件时切换状态。作者文档列出支持 Zotero 10 |
| [Litradar Zotero Bridge](../zotero-litradar/README.md) | Zotero → 工具 → 插件 | 使用 Reading List 状态联动或“下载 PDF”时必需 | 桥接 Reading List 的状态方法，并调用 Zotero 自己的全文查找和下载队列；0.2.0 已在 Zotero 10.0.7 的隔离配置中完成原生安装与启动验证，兼容范围为 10.0.x |
| [Litradar Obsidian 插件](../obsidian-litradar/README.md) | Obsidian → 第三方插件 | 希望从 Obsidian 操作时需要 | 展示文献雷达，调用引擎入库、更新阅读标签、定位 Zotero 条目与打开 PDF |

Reading List 管理阅读状态，Zotero Bridge 将其状态与 Litradar 双向连通，并负责触发全文下载。保存文献、定位 Zotero 条目和打开已有 PDF 使用原生 Zotero 接口，不依赖 Zotero Bridge。

## 1. 开启 Zotero 本地通信

1. 打开 Zotero 10，进入「设置 → 高级」。
2. 勾选「允许此计算机上的其他应用程序与 Zotero 通信」（Allow other applications on this computer to communicate with Zotero）。
3. 使用 Litradar 联动时保持 Zotero 运行。本地 API 默认地址为 `http://127.0.0.1:23119`。
4. 首次修改条目、阅读标签或提交 PDF 下载时，按 Zotero 自己的弹窗授权；选择“始终允许”可供后续操作复用授权。

接口与授权机制见 [Zotero 官方本地 API 文档](https://www.zotero.org/support/dev/web_api/v3/local_api)。

## 2. 安装和设置 Reading List

已安装 Reading List 的用户可直接检查状态列及偏好设置，无需重复安装。

1. 从作者的 [Releases 页面](https://github.com/Dominic-DallOsto/zotero-reading-list/releases/latest) 下载适用于 Zotero 10 的 `.xpi` 文件。兼容范围以该版本的作者说明为准。
2. 在 **Zotero** 中进入「工具 → 插件 → 齿轮 → 从文件安装插件」，选择下载的 XPI。
3. 按作者建议重启 Zotero。
4. 右键点击文献列表的列标题，启用 **Read Status** 列。
5. 在「设置 → Reading List」中按需配置状态名称、快捷键、新增文献的默认状态及打开 PDF 时的状态变化。
6. 通过文献右键菜单或插件快捷键修改阅读状态；可以同时选中多篇处理。

功能和操作细节见 [Reading List 作者教程](https://github.com/Dominic-DallOsto/zotero-reading-list#readme)。

### 与 Litradar 的双向联动

安装 Reading List 和 Zotero Bridge **0.2.0** 后，Litradar 的阅读清单直接读取 Reading List 的真实状态；卡片上的状态选项来自 Reading List 当前配置，包含新增、待读、阅读中、已读、不读以及自定义状态。

- 在 Litradar 选择状态：引擎通过桥接扩展调用 Reading List 的状态方法，并等待 Zotero 保存完成。
- 在 Zotero 用 Reading List 修改状态：Litradar 下次自动刷新或点击“刷新 Zotero”后更新。默认面板轮询为 30 秒，实际间隔以 Litradar 设置为准；这不是实时推送。
- 状态保存在 Reading List 使用的 `Extra` 字段（`Read_Status`、`Read_Status_Date`），不再用 Litradar 标签作为新的状态来源。Reading List 的标签同步偏好仍生效。
- 选择“未设置”清除 Reading List 状态，保留文献及其余 Extra 内容。

### 迁移旧记录

在 Litradar 的 Reading List 区域选择“旧待读映射”和“旧已读映射”，再点击相应迁移按钮：

- “迁移旧 Zotero 标签”：处理 `litradar/待读`、`litradar/待读`、`litradar/已读`；已有 Reading List 状态优先保留，成功后移除旧标签。
- “迁移旧本地清单”：把旧版插件的本地记录写入 Reading List，成功后才删除该条旧记录；已有 Reading List 状态保留。

迁移由用户显式触发；失败记录保留，修改冲突会拒绝覆盖并提示刷新重试。自定义状态须先选择正确的迁移目标。

## 3. 安装 Litradar Zotero Bridge

1. 使用仓库提供的 [litradar-pdf-bridge-0.2.0.xpi](../zotero-litradar/litradar-pdf-bridge-0.2.0.xpi)。
2. 在 **Zotero** 的「工具 → 插件 → 齿轮 → 从文件安装插件」中选择它，并确认已启用。旧 PDF Bridge 使用相同插件 ID，会直接升级，不需要同时保留两份。
3. 此本机定制版采用手动安装更新；详细兼容性、更新策略和验证记录见 [Zotero Bridge 说明](../zotero-litradar/README.md)。

需要从源码重新打包时，在仓库根目录执行：

```bash
python3 zotero-litradar/build.py
```

Zotero Bridge 不会代替 Zotero 管理附件，也不另建 PDF 下载目录。能否获得全文取决于开放获取情况、机构权限、Zotero 解析器及网络；“已提交”只表示任务已交给 Zotero，结果在 Zotero 原生进度窗口查看。

## 4. 配置 Litradar 并验证

1. 按 [Obsidian 插件教程](../obsidian-litradar/README.md#安装) 安装 Litradar，并填写正确的 Python 解释器绝对路径。
2. 在 Litradar 设置中选择「文献保存位置」；列表展示完整父子文件夹路径。默认自动创建 `Litradar 文献雷达 / YYYY-MM-DD`，按日报日期保存；历史批次用该批次日期，未指定批次时按配置时区取当天。已有条目复用并加入目标收藏夹，保留原有归属。也可以手动指定其他文件夹覆盖默认位置。
3. 在文献卡片上点击「保存到 Zotero」，确认条目入库或复用已有条目。
4. 点击「在 Zotero 打开」，确认定位到对应条目。
5. 对缺少 PDF 的条目点击「下载 PDF」，在 Zotero 中查看查找与下载结果。
6. 有 PDF 附件后，点击「打开 PDF」，确认由 Zotero 阅读器打开。
7. 在 Zotero 中用 Reading List 修改状态，再在 Litradar 点击“刷新 Zotero”，确认两边一致；反向在 Litradar 修改，再核对 Zotero 的 Read Status 列。

## 常见问题

- **本地 API 返回 403**：检查步骤 1 的通信开关。
- **找不到 Zotero Bridge 接口**：确认装在 Zotero 中并已启用；使用 0.2.0 包，按需重启 Zotero。
- **无法安装 Zotero Bridge**：先核对版本。0.1.0 和 0.1.1 有已知安装清单问题；0.2.0 仅针对 Zotero 10.0.x，不应直接用于其他主版本。
- **Reading List 显示已读，Litradar 尚未更新**：先点击“刷新 Zotero”，再确认 Reading List 和 Zotero Bridge 0.2.0 均已启用；0.1.x 桥接版没有状态接口。
- **打开 PDF 提示没有附件**：先在 Zotero 确认全文已下载；仅有文献条目或网页链接时还不能打开 PDF。

## 每日文献归档

默认保存结构：

```text
我的文库
└── Litradar 文献雷达
    ├── 2026-11-03
    └── 2026-11-04
```

收藏夹在第一次实际保存时创建，之后重复使用。面板的保存、下载、从日报设置阅读状态的新入库操作，以及 `litradar zotero push` 均默认归档。旧文献复用时只添加目标收藏夹归属，不重复创建文献、不移走原归属；仅在阅读清单中切换已有文献状态不会重新归档到当天。

设置 → Litradar → 文献保存位置，选择“自动归档：Litradar 文献雷达 / 每日日期”启用默认规则；选择具体收藏夹则覆盖默认位置。自动归档根名称可通过引擎配置 `zotero.daily_collection_root` 自定义。创建收藏夹失败时停止入库，不回退成未分类条目。

Zotero 收藏夹是组织视图：文献仍会出现在“我的文库”的全库总览中，可从专属收藏夹集中浏览每日文献。PDF 由 Zotero 附件存储管理，不按收藏夹建立磁盘目录。

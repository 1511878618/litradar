# 发布清单

三层发布互相独立：**引擎包（PyPI）**、**源码（GitHub）**、**Obsidian 插件（GitHub Release / BRAT）**。
引擎版本用 `2.x.y`，插件版本单独用 `0.x.y`，两者不必同步。

---

## 0. 一次性准备

| 事项 | 说明 |
|---|---|
| PyPI 账号 + 项目名 | 确认 PyPI 项目归属；创建项目后在 Settings → Publishing 配置 Trusted Publisher |
| Trusted Publisher | Owner `1511878618` / Repo `litradar` / Workflow `release.yml` / 无需 Environment（当前发布 job 未绑定环境） |
| Git 远端 | `git@github.com:1511878618/litradar.git`（也可使用 HTTPS 认证） |
| BRAT（插件） | 用户侧装 BRAT，添加 `1511878618/litradar` 即可；插件文件要作为 Release asset 上传 |

> 不想用 Trusted Publishing 时，也可以本地 `twine upload`（见 §4B），但 token 不要进仓库。

---

## 1. 预检

```bash
# 在 conda base 里
python -m pip install -e ".[dev]"
python tests/run_tests.py          # 必须全绿
ruff check src tests

# 插件
cd obsidian-litradar
pnpm install
pnpm run build                      # 生成 main.js
npx tsc --noEmit
```

---

## 2. 版本号（一次提交）

| 位置 | 文件 |
|---|---|
| 引擎 | `pyproject.toml` 的 `version`、`src/litradar/__init__.py` 的 `__version__` |
| 文档 | `CHANGELOG.md` 顶部新增小节、README 版本徽章、测试数徽章 |
| 插件（如发布） | `obsidian-litradar/manifest.json` 的 `version`、`versions.json` 追加 `"0.1.1": "1.5.0"` |

CHANGELOG 用「用户能感知的变化」写，不要贴 commit 列表。

```bash
git add -A
git commit -m "release: 2.2.0"
git tag v2.2.0
git push origin main v2.2.0
```

推 tag 会触发 `.github/workflows/release.yml`：跑测试 → 构建 sdist/wheel → 建 GitHub Release（引擎 wheel/sdist、Obsidian 0.4.0 安装包及单文件、Zotero Bridge XPI）。
仅在仓库 Actions 变量 `PUBLISH_TO_PYPI=true` 时向 PyPI 发布；默认不启用。
版本号与 tag 不一致时流水线会直接失败（这是故意的）。

---

## 3. 发布后验证

```bash
# 换一个干净环境，确认装得上
conda create -n litradar-check python=3.12 -y
conda activate litradar-check
pip install litradar==2.2.0
litradar --version --json              # {"engine": "2.2.0", "schema": 2}
litradar init --vault /tmp/check-vault
litradar doctor --vault /tmp/check-vault
```

---

## 4A. 本地手动发布（可选，替代流水线）

```bash
python -m pip install build twine
python -m build                         # dist/litradar-2.2.0.tar.gz + .whl
python -m twine check dist/*
python -m twine upload dist/*           # 需要 PyPI token（账号 → API tokens）
```

token 只放环境变量或 `~/.pypirc`（`chmod 600`），不要提交。

## 4B. Obsidian 插件发布（BRAT）

```bash
cd obsidian-litradar
# 1) 改 manifest.json version（例如 0.1.1）与 versions.json
pnpm run build                          # main.js 已在 .gitignore 里，不作为源码提交
# 2) 建 release，三个资产缺一不可
gh release create obsidian-0.1.1 \
  main.js manifest.json styles.css \
  --title "Obsidian plugin 0.1.1" --notes "…"
```

用户侧：BRAT → Add Beta plugin → `1511878618/litradar`。
注意插件是 `isDesktopOnly: true`，且需要用户自己填 Python 解释器路径（conda base）。

---

## 5. 回滚

- **PyPI 不能删版本**，只能 `yank`：`pip install litradar==2.2.0` 仍可装，但会被提示已撤回；
  发布前务必在干净环境里跑一次 `pip install`。
- GitHub Release 可以删掉重建，已公开 tag 不移动；有问题时创建新修复版本，保留历史可追踪性。
- 插件版本可以删 release 重发；BRAT 以 manifest 版本号为准。

---

## 6. 发布检查表

- [ ] `tests/run_tests.py` 全绿（当前 384）
- [ ] `ruff check src tests` 通过
- [ ] 插件 `tsc --noEmit` + `pnpm run build` 通过
- [ ] `pyproject.toml` / `__init__.py` / CHANGELOG / README 徽章版本一致
- [ ] CHANGELOG 写好「不兼容变更」（如果有）
- [ ] 干净环境 `pip install litradar==x.y.z` 成功
- [ ] GitHub Release 有 sdist + wheel
- [ ] 插件 release 有 `main.js` / `manifest.json` / `styles.css`

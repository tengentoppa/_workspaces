# Workspaces

集中管理多 repo VSCode workspace + 部署 task 的中央倉庫。
這個 repo 自己用 git 追蹤，clone 到任何機器 + 跑 `bootstrap` 就能還原整套開發環境。

## 目錄結構

```
_workspaces/
├── README.md              ← 你看的這份
├── manifest.yaml          ← 來源 repo 清單 (url + 相對路徑 + branch)
├── bootstrap.sh           ← 新機器跑這個 (bash)
├── bootstrap.ps1          ← 新機器跑這個 (Windows PowerShell)
├── .gitignore
└── workspaces/
    ├── sport_scout.code-workspace
    ├── bet_bot.code-workspace
    └── safe_trade.code-workspace
```

`_workspaces/` 必須跟 `b1e8/`、`royal/` **同層**：

```
D:/Documents/Project/        ← 或新機器上 ~/Project/
├── _workspaces/             ← 本 repo
├── b1e8/
└── royal/
```

`.code-workspace` 內的 folder 路徑都是相對 `_workspaces/workspaces/`，
例如 `../../b1e8/sport/sport_scout`，跨機器只要根目錄一致就能直接運作。

## Workspace 索引

| Workspace | 用途 | Registry / 部署目標 | 涵蓋 repo |
|-----------|------|---------------------|-----------|
| [sport_scout](workspaces/sport_scout.code-workspace) | Polymarket sports 套利掃描 + 後台 | `registry.onthesky.net/scout`、`registry.onthesky.net` | sport_spy, sport_scout, sport_logistics, sport_matcher, research |
| [bet_bot](workspaces/bet_bot.code-workspace) | 投注 bot 後端 + 控制台前端 | `registry.onthesky.net/bet_bot` | bet_bot, bot_fe |
| [safe_trade](workspaces/safe_trade.code-workspace) | Royal 錢包 + safe_trade 前後端 | `registry.onthesky.net/royal` | safe_trade, sport_scout_royal, core_wallet, crypto_hound, union_wallet, devops |

## 使用方式

### 已經在現有機器上開 workspace

```
File → Open Workspace from File... → _workspaces/workspaces/<name>.code-workspace
```

跑 task：`Ctrl+Shift+P → Tasks: Run Task` → 從清單選。

### 新機器 / 新 WSL 環境

```bash
# 1. 建好專案根目錄
mkdir -p ~/Project && cd ~/Project

# 2. 拉這份 workspaces repo
git clone <_workspaces 的 remote> _workspaces

# 3. 跑 bootstrap 把所有來源 repo clone 到正確相對位置
cd _workspaces
./bootstrap.sh        # Windows: .\bootstrap.ps1

# 4. 開 workspace
code workspaces/sport_scout.code-workspace
```

`bootstrap` 不會切換 branch、不會強制覆寫已存在的 repo；
要更新本地：`./bootstrap.sh --fetch` 會對已存在的 repo 跑 `git fetch --all --prune`。

## Task 命名規則

每個 workspace 內的 task 都遵循：

| 前綴 | 用途 | 範例 |
|------|------|------|
| `<short>: build & push image` | docker build + tag |
| `<short>: docker push` | push 到 registry (依賴上面那個) |
| `<short>: dev` | 本地開發伺服器 (start:dev / dev) |
| `<short>: lint` | lint |
| `<short>: test` | test |
| `git: status (all repos)` | 一次列出 workspace 內所有 repo 的 git status |
| `git: fetch all` | 一次對所有 repo 做 `git fetch --all --prune` |
| `deploy: <env>` | (TBD) 之後加遠端部署用 |

跑法：`Ctrl+Shift+P → Tasks: Run Task` → 選對應 task。

## 新增 workspace 流程

1. 在 `workspaces/` 新增 `<name>.code-workspace`
2. `folders` 用相對路徑 `../../b1e8/...` 或 `../../royal/...`
3. 把該專案的 `build.sh` 內容搬進 `tasks` 區段
4. 把每個來源 repo 加進 `manifest.yaml`
5. 在本 README 的「Workspace 索引」加一列
6. `git add . && git commit && git push`

## 注意事項

- `.code-workspace` 是 JSONC，可以寫 `//` 註解，**請務必**在檔頭註記 registry / 部署流程 / 涵蓋 repo
- workspace 裡的 git 多 repo 操作，VSCode 原生 Source Control 面板已經會自動列每個 folder 的變更，task 裡的 `git: status (all repos)` 是給 terminal 一次掃過用
- 不要在 workspace 內寫絕對路徑 (`D:/...` 或 `/home/...`)，會破壞跨機器可攜性
- 不要 commit `.env` 或私人變體 — 用 `*.local.code-workspace` 命名，已被 `.gitignore` 擋掉

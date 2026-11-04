# building-db

Link design decisions and their basis to drawing elements, so they can be traced later.
Built for the hackathon "Built with Claude: Japan 2026".

図面の要素に、判断とその根拠を紐づけ、後から追えるようにするためのツールです。ハッカソン「Built with Claude: Japan 2026」の作品です。

## Setup and first run / 準備と最初の実行

Windows PowerShell, Python 3.11 or later. Run the commands from the repository
folder, top to bottom. The virtual environment is used through its own
`python.exe`, so it never needs to be activated.

Windows PowerShell と Python 3.11 以降で動きます。リポジトリのフォルダで、上から順に実行してください。仮想環境は `.venv\Scripts\python.exe` を直接使うので、有効化（Activate）は要りません。

You need: Python 3.11 or later (`python --version`), and Git to clone the repository. / 必要なもの：Python 3.11 以降（`python --version` で確認）と、リポジトリを複製するための Git。

```powershell
# 1. Create the virtual environment / 仮想環境を作る
python -m venv .venv

# 2. Install the dependencies and this package (editable) / 依存とこのパッケージを入れる
.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 3. Import a sample drawing / サンプルの図面を取り込む
.venv\Scripts\python.exe -m building_db import samples\step2\step2.dxf --db out\step2.sqlite --wall-types samples\step1\wall_types.csv --finish-schedule samples\step1\finish_schedule.csv --default-wall-type WT-3

# 4. Show areas, perimeters and wall lengths / 面積・周長・壁の長さを表示する
.venv\Scripts\python.exe -m building_db report --db out\step2.sqlite

# 5. Run the tests / テストを実行する
.venv\Scripts\python.exe -m pytest
```

Comparing versions (the new drawing is imported into the same database; a
snapshot of the previous version is taken first):

版の比較（新しい版は同じ DB に取り込みます。取り込む前に、前の版の写しが自動で `out\step2.sqlite.snapshots\` に残ります）:

```powershell
# The partition's boundary moved to x = 2800 but its wall center line did not: a 500 mm deviation
.venv\Scripts\python.exe -m building_db import samples\step2\step2_boundary_only.dxf --db out\step2.sqlite --wall-types samples\step1\wall_types.csv --finish-schedule samples\step1\finish_schedule.csv --default-wall-type WT-3

# Differences against the snapshot (replace the file name with the one printed above)
.venv\Scripts\python.exe -m building_db diff --base out\step2.sqlite.snapshots\v1_YYYYMMDD-HHMMSS.sqlite --new out\step2.sqlite

# Record a decision on the deviation / ずれについての判断を記録する
.venv\Scripts\python.exe -m building_db decide --db out\step2.sqlite --target A-WL-0005 --choice align_to_boundary --reason "Follow the plan"
.venv\Scripts\python.exe -m building_db report --db out\step2.sqlite
```

## Sample data / サンプルデータ

All sample data is fictional and contains no company information.

- `samples/step1/`: Test data created after the hackathon started (2026-11-03), with the help of Claude in the claude.ai chat (not Claude Code).
- `samples/step2/`: Test data created after the hackathon started (2026-11-03). The DXF files (`step2.dxf`, `step2_moved.dxf`, `step2_boundary_only.dxf`) were drawn by the designer in Jw_cad. `expected.md` and `expected_diff.md` were written with the help of Claude in the claude.ai chat (not Claude Code).

サンプルデータはすべて架空のもので、会社の情報は含みません。

- `samples/step1/`：ハッカソン開始後（2026-11-03）に、claude.ai のチャットの Claude（Claude Code ではない）の補助で作成したテストデータです。
- `samples/step2/`：ハッカソン開始後（2026-11-03）に作成したテストデータです。DXF（`step2.dxf`、`step2_moved.dxf`、`step2_boundary_only.dxf`）は、設計者が Jw_cad で描きました。`expected.md` と `expected_diff.md` は、claude.ai のチャットの Claude（Claude Code ではない）の補助で作成しました。

## Libraries / 使用ライブラリ

| Library | Purpose / 用途 | License / ライセンス |
|---|---|---|
| [ezdxf](https://github.com/mozman/ezdxf) | Read DXF files / DXF の読み取り | MIT |
| [pytest](https://github.com/pytest-dev/pytest) | Tests / テスト | MIT |

Packages installed together with them / 一緒にインストールされる部品:

| Package | License |
|---|---|
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| fonttools | MIT |
| pyparsing | MIT |
| typing_extensions | PSF-2.0 |
| pluggy | MIT |
| iniconfig | MIT |
| packaging | Apache-2.0 OR BSD-2-Clause |
| Pygments | BSD-2-Clause |
| colorama | BSD |

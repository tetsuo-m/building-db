# building-db

Link design decisions and their basis to drawing elements, so they can be traced later.
Built for the hackathon "Built with Claude: Japan 2026".

図面の要素に、判断とその根拠を紐づけ、後から追えるようにするためのツールです。ハッカソン「Built with Claude: Japan 2026」の作品です。

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

# 実装計画：版の比較と判断の記録

## Context（なぜやるか）

核は「図面の要素に、判断とその根拠を紐づけ、後から追えるようにする」こと。判断を要素に付けるには、版が変わっても同じ要素が同じ ID を保つ必要がある。今のコードは節点を位置で探すので、位置が動くと ID が変わり、前の版の行も残る（部屋3室、面積の合計 33.2 ㎡）。

ゴールは、`tests/test_step2_diff.py` の xfail 53件をすべて通し、印をなくすこと。前提は SPEC.md の 8. と `samples/step2/expected_diff.md`。

## 進め方の決まり

- 手順ごとに：テストを先に書く → 失敗を確認 → 実装 → 全件実行 → **結果を報告して、印を外すこととコミットの承認をいただく**。
- strict=True なので、実装で通るようになったテストは「失敗（XPASS）」と表示される。これが印を外す合図になる。
- 既存のテストの中身は、理由を示して承認をいただかない限り変えない。増やすのは新しいファイルにする。
- 依存ライブラリは増やさない（スナップショットは標準の `sqlite3` の `backup`）。push はご指示があるまでしない。

## 全体の順序と、印を外すテスト

| 手順 | 内容 | 外す印 | 件数 |
|---|---|---|---|
| 1 | DBの形の版 | なし | 0 |
| 2 | ID の引き継ぎ（照合） | NEEDS_ID_MATCHING 全部 | 29 |
| 3 | 一致させた理由の記録、最後に見た版 | なし | 0 |
| 4 | ずれの要確認、採用位置の上書きの修正 | NEEDS_DEVIATION 全部 | 4 |
| 5 | スナップショットと差分 | NEEDS_VERSIONS の一部 | 6 |
| 6 | 判断の記録 | NEEDS_VERSIONS の残り | 14 |
| 7 | コマンド、SPEC.md の整理 | なし | 0 |

この順にする理由：
- 判断は ID に付くので、照合が先。ずれは「同じ壁」と判定できて初めて1件にまとめられる。
- 形の版を最初に入れる。以降の手順で表が変わるので、古いDBで変な壊れ方をしないようにする。

---

## 手順1：DBの形の版（ご要望3）

- SQLite の `PRAGMA user_version` に版の番号を記録する。定数 `SCHEMA_VERSION` は [db.py](../../src/building_db/db.py) に置く。
- `connect()` は、`schema.sql` を流す**前に**確かめる。古いDBを1バイトも変えないため。
  - 表がないDB：表を作り、版を書く。
  - 版が一致：そのまま開く。
  - 表があるのに版が違う（今の版0のDBを含む）：`SchemaVersionError` で止める。
- 読むだけの入口 `open_existing()` を足し、`report` と差分でも同じ確認をする。今の `_report` は `sqlite3.connect` を直接使っている。ファイルがなくても空のDBを作らない。
- コマンドは、エラーの全文でなく、次の形の文を出して終了コード1で終わる。

```
This database uses schema version 0, but this program needs version 1.
Nothing was changed. Import the drawings into a new database file.
```

- **自動の移行はしない。** 止めるだけにする（生成物なので、取り込み直せば戻る）。表を変えるコミットごとに版を上げる。
- テスト（新規 `tests/test_schema_version.py`）：古い形のDBに取り込むと止まる／DBのファイルが変わらない（SHA-256で確認）／report も止まる／コマンドの終了コードと文面／新しいDBには版が入る。

## 手順2：ID の引き継ぎ（NEEDS_ID_MATCHING）

新しいモジュール `src/building_db/matching.py` に、DBに書かない照合だけの関数を作る。[importer.py](../../src/building_db/importer.py) の `_node_id`、`_edge_id`、`_find_wall` をこれで置き換える。既存の `build_network`（[network.py](../../src/building_db/network.py)）、`next_id`（db.py）、`_find_room` は使い続ける。

1000 mm は `MAX_MOVE_MM` として、コードではここ一か所だけに書く。

**節点**（SPEC 8. の (a)(b)）
1. 足場：許容差の内側で位置が同じ節点は一致。X1/Y1 は原点を決める足場で、通り芯は符号で一致させる。
2. 広げる：残りの節点について、次をすべて満たす前の版の節点を候補にする。満たすものが**互いに一つだけ**なら一致。一致が増えなくなるまで繰り返す。
   - 確定済みの隣の節点の組が同じ
   - 集まる境界線の数が同じ
   - それぞれの隣への向きが同じ
   - 移動が 1000 mm 以内
3. 残り：ID を引き継がず、「追加の候補」と「削除の候補」にして要確認に出す。候補が複数なら、候補も示す。

例：step2_moved の (2800, 0) は、隣が (0,0) と (6000,0) で、これを満たす前の版の節点は (2300, 0) だけ。

**境界線**：両端の節点が同じなら一致。移動で端点の大小が入れ替わったら、`node_start`/`node_end` を網の向きに直す（`side` がずれないように）。

**部屋**：囲む境界線の組で一致（今の `_find_room`）。室番号だけで引き継いだときは、これからは要確認も出す（分割・統合の疑いがあるため）。

**壁**（SPEC 8. の (c)）
- 道1：追従する境界線の組で探す（今の `_find_wall`）。
- 道2：道1で決まらない線を、向き・接する壁の組・位置の近さで、前の版の壁と直接照合する。
- 二つの道の結果が食い違えば、ID を引き継がず要確認にする。
- 道2で一致した壁は、`wall_edge_spans` を消さずに残す（step2_boundary_only の間仕切）。

**(d) の例外**は決めるまで要確認にする。起点がない、分割・統合、節点の増減、またぐ境界線の数の変化、1000 mm を超える移動、名前と形の同時の変更。

**外す印（29件）**：`test_counts`、`test_node_ids_kept`、`test_edge_ids_kept`、`test_room_ids_kept`、`test_wall_ids_kept`、`test_t_junctions_moved`、`test_edge_lengths`、`test_partition_edge_position`、`test_left_room`、`test_totals`、`test_wall_types`、`test_outer_walls_not_moved`（以上12件×2ファイル）、`test_moved_partition_wall`、`test_boundary_only_observed_wall`、`test_boundary_only_adopted_wall`、`test_boundary_only_wall_still_follows_the_edge`、`test_boundary_only_wall_length`。

**一度にまとめる理由**：印はテストの関数ごとに付いている。節点だけ先に直すと、`test_counts` などが step2_moved でだけ通り、strict のためコミット時に全体が赤くなる。そこで、壁の道2までを1つのコミットにする。

**注意**：この手順では、壁芯線の「does not follow any boundary line」の要確認を残す。NEEDS_DEVIATION の4件は手順4まで失敗のままで、印もそのまま。

新規テスト `tests/test_step2_matching.py`（ezdxf で一時的に作った図面を使う。`test_step1_review.py` の `build_dxf` の方法）：
- 1000 mm を超える移動は引き継がず要確認
- 候補が一つに決まらないときは引き継がず、候補を示す
- 足場がないときは要確認
- 動いた節点が要確認に出ないこと（差分にだけ出る。`test_moved_review_flags` が1件を求めるため）

## 手順3：一致させた理由の記録（ご要望4）

新しい表 `match_log`（版を上げる）。取り込みごとの履歴で、上書きしない。

| 列 | 内容 |
|---|---|
| `batch_id` | どの取り込みか |
| `target_table`, `target_id` | 対象の要素 |
| `classification` | `match` / `moved` / `added_candidate` / `missing_candidate` / `undecided` |
| `rule` | `grid_label` / `same_position` / `connectivity` / `both_end_nodes` / `boundary_set` / `wall_span` / `wall_direct` |
| `anchors` | 根拠にした要素の ID（JSON）。例：(2800, 0) なら隣の2節点 |
| `moved_mm` | 前の版からの移動量 |
| `candidates` | 決まらなかったときの候補（JSON） |

- **件数**は保存せず、この表から数える。`ImportResult.match_counts` に `auto`（自動処理）と `to_person`（人に渡した）を入れ、コマンドの出力にも出す。
- field_provenance は「値がどこから来たか」、match_log は「なぜ同じ要素と判断したか」で役割を分ける。

**最後に見た版**：nodes、edges、rooms、walls に `last_seen_batch` を足す。理由は、DBから消さない要素が、合計や report に混ざるのを防ぐため。`wall_lengths_m` と report は最新の版で見た要素だけを数え、見つからない要素は report に別に示す。差分の `missing` もこの列で求める。

テスト（`test_step2_matching.py` に追加）：
- step2_moved の T字の節点が `connectivity`、移動量 500、足場に隣の節点の ID
- 角の節点が `same_position`、移動量 0
- 件数の値
- 同じ図面を2回取り込むと履歴が2回分あること
- 図面から消えた壁が DB に残り、合計に入らないこと

外す印：なし。

## 手順4：ずれの要確認と、採用位置の修正（ご要望2、NEEDS_DEVIATION）

**食い違いの修正**：[importer.py の `_import_walls`](../../src/building_db/importer.py#L265) は、取り込みのたびに `adopted_geometry` を図面の値で上書きしている。次のように変える。

| 状況 | 観測位置 | 採用位置 | 要確認 |
|---|---|---|---|
| 新しい壁 | 図面 | 図面 | なし |
| 追従中で、図面の線が境界線と一致 | 図面 | 図面（きれいな変更） | なし |
| 追従中で、ずれている（判断なし） | 図面 | **変えない** | ずれ1件 |
| 判断あり（手順6） | 図面 | 判断に従う | なし |

- 変えないときは、`adopted_geometry` の出典も上書きしない。
- **先に足すテスト**：壁芯線だけを 2500 に動かした図面（ezdxf で作る）を取り込むと、観測 2500、採用 2300 のまま、ずれ 200 mm。既存の `test_boundary_only_adopted_wall` は、図面の値も 2300 なので、上書きしていても通ってしまい、この修正を確かめられないため。
- 直ったら、SPEC.md の「既知の食い違い」の行を消す。

**ずれの要確認**：壁の期待位置は、追従する境界線の今の位置から計算し、保存しない。ずれがあれば `ImportResult.wall_deviations` に1件入れ、要確認の文に壁の ID、境界線の ID、ずれの量を入れる。その壁と境界線については、「does not follow any boundary line」と「has no wall」を出さない。

**外す印（4件）**：`test_moved_no_wall_deviation`、`test_boundary_only_two_review_flags`、`test_boundary_only_deviation_not_split`、`test_boundary_only_deviation_record`。

## 手順5：スナップショットと差分（NEEDS_VERSIONS の一部）

新しいモジュール `src/building_db/versions.py`。

- `take_snapshot(db_path, snapshot_path)`：`sqlite3` の `backup` でコピーする。
- `diff_versions(base_db, new_db)`：二つのDBを ID で突き合わせ、`.changes`（kind、element_id、field、before、after）、`.added`、`.missing` を返す。面積・周長・長さは `calc.py` でその場で計算する。

**外す印（6件）**：`test_snapshot_keeps_the_base_version`、`test_moved_diff_nothing_added_or_missing`、`test_moved_diff_nodes`、`test_moved_diff_edge_lengths`、`test_moved_diff_wall`、`test_moved_diff_rooms`。

## 手順6：判断の記録（NEEDS_VERSIONS の残り）

新しい表 `decisions`（版を上げる）：`decision_id`、`target_table`、`target_id`、`choice`、`reason`、`basis_kind`（`person`）、`decided_on`、`premise_valid`、`premise`（判断した時点の観測位置・期待位置の JSON）。

`wall_status` は保存せず、判断と今の位置からその都度求める（計算値を保存しない決まりに合わせる）。

3つのコミットに分ける。

| 小手順 | 内容 | 外す印 |
|---|---|---|
| 6a | `record_decision`、`decisions_for`（記録だけ） | `test_decision_fields`、`test_decision_reason_is_optional`、`test_align_does_not_rewrite_the_drawing`、`test_decision_stays_on_the_wall_after_reimport`（2件） |
| 6b | 「境界線に合わせる」：採用位置を期待位置へ。出典は `designer`。`wall_status`、`drawing_fix_proposals` | `test_align_moves_adopted_only`、`test_align_proposes_drawing_fix`、`test_align_status_until_the_drawing_is_fixed`、`test_align_survives_reimport`、`test_align_reimport_one_review_flag`、`test_align_status_clears_when_the_drawing_is_fixed` |
| 6c | 「追従を外す」：`follow_edge` を 0 に。`wall_edge_spans` は記録として残す。`accepted_exceptions`、report への表示 | `test_unfollow_keeps_the_wall`、`test_unfollow_reimport_keeps_the_exception`、`test_unfollow_stays_in_report` |

ずれのない壁への判断は、エラーで止める。テストを足す。

## 手順7：コマンドと SPEC.md

CLAUDE.md の「取り込み・照合・差分・出力はコマンドを通す」に合わせて、[cli.py](../../src/building_db/cli.py) に足す。

- `snapshot --db ... --to ...`
- `diff --base ... --new ...`
- `decide --db ... --target A-WL-0005 --choice align_to_boundary --reason "..."`
- `import` は、既存のDBに取り込む前に、自動でスナップショットを `<DB名>.snapshots/` に取る。

テストは新規 `tests/test_step2_cli.py`。SPEC.md の 3. と 8. を実装に揃える（案のうち実装したものを Known へ、DBの形の版、`match_log`、`decisions`）。

---

## 決まったこと（2026-11-04、承認時の回答）

1. **`moved_mm` の保存は良い。** 現在の状態から計算する値ではなく、取り込みの時点の事実の記録として扱う。`decisions` の `premise` も同じ。この考え方を SPEC.md に書く。
2. **`premise_valid` は次の範囲で良い。** 判断のときの期待位置が後の図面で変わったら 0 にして、要確認に出す（テストを足す）。図面が直ったときは 0 にしない。
3. **仮の形のテストは変えない。** `expected_x_mm`、`position_mm`、`edge_id` はそのままで、`expected_geometry` と `edge_ids` を足して一般の場合に備える。縦でない壁の `expected_x_mm` は `None`。

## 追加の指示（2026-11-04）

- この計画を `docs/plans/versions.md` に保存して、コミットする。
- 必ずやるのは手順1、2、4、5、6a、6b、7。手順3は最小限（理由の種類と件数）でもよい。手順6cは、時間が足りなければ後回しにする。
- 手順2は一つのコミットにまとめてよい。ただし途中で、節点→境界線→部屋→壁の順に、`test_step2_matching.py` のテストで一つずつ確かめながら進める。
- 実装は手順1から始める。

## 手順2で計画から変えたこと（2026-11-04、実装時）

1. **境界線の向きの入れ替えは実装しなかった。** 計画では「移動で端点の大小が入れ替わったら `node_start`/`node_end` を直す」としていた。実装した節点の照合は、隣への向きが同じ節点しか一致させないので、両端の大小が入れ替わる移動では節点が一致せず、境界線は新しい ID になる。つまり入れ替えが必要になる場面が生じない。生じたとしても、新しい境界線として要確認の対象になる側に倒れ、`side` がずれたデータが作られることはない。
2. **(c) の「二つの道の食い違い」は、手順4の「ずれ」として扱う。** 計画では「追従する境界線経由の照合と、直接の照合の結果が食い違えば要確認」としていた。境界線の ID は両端の節点が一致したときだけ保たれるので、「境界線経由では壁 A、直接では壁 B」という取り違えを作れなかった。実際に起きる食い違いは、step2_boundary_only のように「追従する境界線は動いたのに、壁芯線が動いていない」というもので、これは壁と境界線の「ずれ」そのものである。手順4で `wall_deviations` として扱う。
3. **DBの形の版は上げなかった。** 手順2では表を変えていないため。

## 手順3で計画から変えたこと（2026-11-04、実装時）

1. **動いていない壁は足場として ID を引き継ぐ**（SPEC.md 8 (c) を改めた）。当初の実装は、またぐ境界線の数や接する壁の組が変わった壁を要確認にしていたが、それでは間仕切の追加・撤去のたびに外壁の ID が変わり、壁に付けた判断が切れる。設計書 T11 とも合わない。線の位置が許容差の内側で同じ壁は、接続の変化に関わらず同じ壁とし、`match_log` には `same_position` と記録する。接する壁の組での照合は、動いた壁にだけ使う。
2. **またぐ境界線の数の変化は、要確認ではなく差分に出す。** `wall_edge_spans` は取り込みで更新されるので、手順5の `diff_versions` で、壁ごとのまたぐ境界線の数（または組）の変化を `.changes`（kind `wall`、field `edge_ids`）として出す。
3. **(d) の例外を狭めた。** 「動いたうえに、またぐ境界線の数も変わった壁」だけを要確認にする。テスト `test_moved_wall_spanning_a_changed_number_of_edges_is_not_matched` で確かめる。
4. **`match_log` に `review` 列を足した。** 「人に渡した件数」を、分類だけでは数えられない（室番号だけで ID を引き継いだ部屋は、ID を保ちつつ要確認になる）ため。

## 確かめ方

- 手順ごとに `.venv\Scripts\python.exe -m pytest` を実行し、通った件数と xfail の件数を報告する。xfail は 53 → 53 → 24 → 24 → 20 → 14 → 0 と減る見込み（手順1の前が先頭）。通った件数は、外した印の分と新しいテストの分だけ増える。
- 最後に、コマンドだけで通しの確認をする（DBは `out\` に作る）。
  1. `import` step2.dxf
  2. `import` step2_boundary_only.dxf（スナップショットが自動で取れる）
  3. `diff`：間仕切の境界線と部屋の変化が出る。ID が同じ
  4. `report`：要確認が2件（室名なし、ずれ 500 mm）
  5. `decide`：「境界線に合わせる」
  6. `import` step2_moved.dxf：「承認済み・図面未反映」が消える
  7. 古い形のDBを `import` すると、分かりやすいエラーで止まる

# 段階1のサンプル（一部屋）

すべて架空のデータです。ハッカソン期間中（2026年11月）に、Claudeの補助を受けて作成しました。会社の情報は含みません。

| ファイル | 内容 |
|---|---|
| step1.dxf | 一部屋の図面（DXF R12、日本語はShift_JIS） |
| wall_types.csv | 壁種表（WT-3のみ） |
| finish_schedule.csv | 仕上表（101 事務室のみ） |
| expected.md | 期待値（テストの正解） |

## Jw_cadでの往復の確認（推奨）

step1.dxf はプログラムで作った「きれいな」DXFです。Jw_cadが書き出すDXFは、レイヤ名や単位の扱いが違う場合があります。

1. Jw_cadで step1.dxf を読み込み、レイヤと文字（101 事務室）が正しく表示されるか確認する。
2. step1.jww として保存する。
3. Jw_cadからDXFとして書き出し、step1_jw.dxf として保存する。

テストは、step1.dxf と step1_jw.dxf の両方で通るようにします。

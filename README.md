# backup

## Description

- MacBook 用のローカルバックアップツールで、`backup.config` に記載された元パスと先パスを順番に処理し、差分コピーと退避（削除されたファイルの移動）を行います。

## Preperation

### Edit backup.config file

- Generate backup.config based on backup.config.example
- Place backup.config in the same directory as backup.py

## Usage

```shell
sudo python backup.py backup.log
```

### 機能

- `backup.config` に行単位で記載された元パス→先パスのペア（タブ区切り）のそれぞれに対して差分コピーを実施
- コピー後、元に存在しないファイルやディレクトリは `.deleted_at_YYYYMMDD` 付きディレクトリへ移動して退避
- `backup.config` の行ごとにエラー/成功メッセージを出力し、指定した `backup.log` に ADD/UPDATE/DELETE の履歴を時刻付きで追記
- `/Volumes` 配下のターゲットはマウントチェックを行うため、未マウントの場合はスキップして通知
- 設定に `IGNORE_LIST` を設定すれば、特定の名前を持つファイル/ディレクトリをコピー/退避対象から除外可能（コード内で調整）

### テスト

- 一時構成（`test_src1`/`test_dst1` など）を `backup.config` に並べて `python backup.py test_report.log` を実行
  - 追加（ADD）、上書き（UPDATE）、退避（DELETE）のログが `test_report.log` にすべて記録されること
  - 退避先には `test_dst1.deleted_at_<日付>/` などが生成される
  - 元の `backup.config` や実環境のデータに影響を与えないよう、テスト後にファイルと一時ディレクトリを削除

# Zenn自動公開リトライ機構

Zennのレートリミット（24時間に5本）によるデプロイ失敗を自動検知・リトライする仕組み。

## 概要

```
予約公開実行
    ↓
デプロイ失敗（レートリミット超過）
    ↓
検証スクリプトが失敗を検知
    ↓
published: true → false にロールバック
    ↓
リトライキューに追加
    ↓
Discord通知
    ↓
次回実行時に自動リトライ（次の空きスロットに予約）
```

## ファイル構成

| ファイル | 用途 |
|---------|------|
| `zenn-verify-published.py` | 公開状態検証 + ロールバック + Discord通知 |
| `zenn-retry-failed.py` | リトライキュー処理 + 次回スロットに予約 |
| `.zenn-retry-queue.json` | 失敗記事のキュー（Git管理） |
| `../workflows/publish-with-retry.yml` | 6時間毎の自動実行ワークフロー |

## 使い方

### 自動実行（推奨）

GitHub Actionsが6時間毎に自動実行:
1. リトライキューの記事を処理（最大3本）
2. 予約公開記事を処理
3. 5分待機してZennデプロイ完了を待つ
4. 公開状態を検証
5. 失敗した記事をロールバック + リトライキューに追加
6. Discord通知

### 手動実行

#### ローカルで検証（~/dev/scripts/版）

```bash
# 公開状態を確認（ロールバックなし）
python3 ~/dev/scripts/zenn-verify-published.py

# 公開状態を確認 + ロールバック
python3 ~/dev/scripts/zenn-verify-published.py --fix

# リトライキューを処理
python3 ~/dev/scripts/zenn-retry-failed.py --max 3
```

#### GitHub Actionsで手動実行

1. https://github.com/correlate000/public-zenn-docs/actions
2. "Publish Scheduled Articles with Retry" を選択
3. "Run workflow" をクリック

## リトライキューの確認

```bash
# リトライキュー確認（ローカル）
cat ~/dev/scripts/.zenn-retry-queue.json

# リトライキュー確認（リポジトリ）
cat .github/scripts/.zenn-retry-queue.json
```

## Discord通知

失敗検知時に `#コンテンツ速報` に以下を通知:
- 失敗した記事のリスト
- リトライキュー状況
- ダッシュボードリンク

## レートリミットルール

- **MUST: 24時間に5本以内**
- **推奨: 1日3本**（08:00 / 12:30 / 19:00）
- リトライ処理は1回に最大3本まで

## トラブルシューティング

### 記事が公開されない

1. リトライキューを確認: `.github/scripts/.zenn-retry-queue.json`
2. GitHub Actionsログを確認
3. Discord通知を確認
4. 手動でpublished_atを調整

### リトライキューが溜まり続ける

1. Zenn-GitHub連携を確認: https://zenn.dev/dashboard/deploys
2. front matterのslugが正しいか確認
3. 手動で公開してキューをクリア

## 再発防止チェックリスト

- [ ] 予約公開前に `grep -r "published_at" articles/ | wc -l` で総数確認
- [ ] 24時間に5本以内に分散
- [ ] GitHub Actionsが正常に動作しているか確認（週1回）
- [ ] Discord通知が届いているか確認

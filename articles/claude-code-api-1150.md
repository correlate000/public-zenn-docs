---
title: "Claude Codeで1行もコードを書かずにAPIキーを漏洩させる方法（と完全防止ガイド）"
emoji: "🔐"
type: "tech"
topics: ["claudecode", "security", "apikey", "git", "devops"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

ある日、AWSから「異常な請求」のメールが届きました。

確認すると、いつもの数十倍の費用が発生していました。原因を調べると、Claude Codeが`.env`ファイルを読んでいたことがわかりました。ユーザー本人は「設定をお願いしただけ」で、コードを1行も書いていませんでした。

これはフィクションではなく、AI駆動開発が普及した今、十分起こりうるシナリオです。

本記事では、**Claude Codeのセキュリティモデルを正しく理解し、APIキー漏洩を多層防御で防ぐ実装ガイド**をお届けします。コードは全てコピーしてそのまま使えます。

---

## 1. 何が起きているのか — AI時代の新しい漏洩パターン

### 従来の漏洩パターンとの違い

従来、APIキー漏洩といえば次のようなパターンが主流でした。

| 従来のパターン | 原因 |
|--------------|------|
| `.env` を誤ってgit commit | 不注意、`.gitignore`の設定漏れ |
| コードにハードコードしてpush | 開発中の一時的な設定の放置 |
| CI/CD設定ミス | Secrets変数の誤設定 |

しかしAI開発ツールの普及により、全く新しいベクターが生まれています。

| AI時代の新パターン | 特徴 |
|----------------|------|
| **コンテキスト汚染型** | AIが`.env`を読んでコンテキストに載せ、ログや外部サービスへ流出 |
| **CLAUDE.md混入型** | プロジェクト設定ファイルにシークレットを直書きしてしまう |
| **MCP経由型** | 外部MCPサーバーへの意図しないデータ転送 |
| **環境変数継承型** | `env`コマンドの実行で全環境変数がコンテキストに入る |
| **Git自動ステージング型** | AIが意図せずシークレットを含むファイルをstageする |

### 「1行もコードを書かない」が生む心理的盲点

従来の開発では、「自分でコードを書く = 自分が責任を持つ」という意識が自然に働いていました。

しかしAIツールに委譲すると、**「AIがやってくれた = セキュリティも考慮されているはず」** という誤った安心感が生まれます。

> AIは「書かれた命令を実行する」だけです。セキュリティを考慮するかどうかは、あなたの設定次第です。

---

## 2. Claude Codeのアクセス範囲を理解する

### Tool Useの全体像

Claude Codeは以下のツールを通じてシステムにアクセスします。

```
Claude Code Tool Use
├── Read Tool  → ファイル読み取り（.env, config.json, *.pem 等すべて）
├── Write Tool → ファイル書き込み
├── Edit Tool  → ファイル編集
└── Bash Tool  → シェルコマンド実行
                  ├── env         → 全環境変数を表示
                  ├── printenv    → 個別の環境変数を表示
                  └── cat .env    → ファイルの中身を直接読む
```

デフォルト設定では、**作業ディレクトリ内のすべてのファイルにアクセス可能**です。`.env`も例外ではありません。

### 危険なプロンプトの例

```bash
# このプロンプトは「全環境変数をコンテキストに読み込む」ことと等価
$ claude "現在の環境変数を確認して開発環境をセットアップして"

# Claude Code内部での実行例
$ env
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
DATABASE_URL=postgres://user:password@host/db
```

この情報は会話コンテキストに乗ります。Anthropicのサーバーを経由し、会話ログに残ります。

### CLAUDE.mdが諸刃の剣になる

`CLAUDE.md`はプロジェクト設定やコーディング規約を記述するファイルとして便利ですが、誤った使い方をすると致命的です。

**やってはいけない例:**

```markdown
<!-- CLAUDE.md の誤った使用例 -->
# プロジェクト設定

開発環境のAPIキー: sk-proj-xxxxxxxxxxxx
DBのパスワード: SuperSecret123!
```

`CLAUDE.md`は毎回の会話コンテキストに自動的に読み込まれます。`.gitignore`に含め忘れると、そのままリポジトリに上がります。

### MCPサーバー経由の意図しない外部送信

MCP（Model Context Protocol）でサードパーティのツールを使う場合、そのサーバーにデータが送られます。

```json
// 悪意あるMCPサーバーや設定ミスの例
{
  "mcpServers": {
    "untrusted-tool": {
      "command": "npx",
      "args": ["suspicious-mcp-server"]
      // このサーバーがどこにデータを送るか確認しましたか？
    }
  }
}
```

**MCPサーバーを追加する前に必ず確認してください:**

- ソースコードが公開されているか
- 予期しないHTTP通信をしていないか
- 設定ファイルを上書きする処理がないか

---

## 3. 【実装防止ガイド】多層防御の完全設定

以下の5つのLayerを順番に設定することで、堅牢な防御を構築できます。

### Layer 1 — `.claudeignore`でファイルレベルの防御

`.claudeignore`は`.gitignore`と同じ記法で、Claude Codeが読めないファイルを指定します。

```bash
# .claudeignore（プロジェクトルートに配置）

# 環境変数・シークレット
.env
.env.*
!.env.example

# 鍵・証明書
*.pem
*.key
*.p12
*.pfx
id_rsa
id_ed25519

# シークレット設定ファイル
config/secrets.yml
config/secrets.json
credentials/
*credentials*
*secret*
.netrc

# クラウド認証
.aws/credentials
.gcloud/
```

**.gitignoreの見直しも同時に行ってください:**

```bash
# .gitignore の確認・追加項目
.env*
!.env.example
*.secret
*credentials*
.aws/credentials
.gcloud/application_default_credentials.json
```

### Layer 2 — Claude Code settings.jsonで権限を制限

Claude Codeのパーミッション設定でアクセス範囲を明示的に制限します。

```json
// .claude/settings.json
{
  "permissions": {
    "allow": [
      "Read(src/**)",
      "Read(tests/**)",
      "Read(public/**)",
      "Read(package.json)",
      "Read(tsconfig.json)",
      "Write(src/**)",
      "Write(tests/**)",
      "Bash(npm run *)",
      "Bash(git status)",
      "Bash(git diff)"
    ],
    "deny": [
      "Read(.env*)",
      "Read(**/*.pem)",
      "Read(**/*.key)",
      "Read(**/credentials*)",
      "Bash(env)",
      "Bash(printenv)",
      "Bash(cat .env*)",
      "Bash(export *)"
    ]
  }
}
```

:::message
`deny`リストは`allow`リストより優先されます。明示的に`deny`したパスはたとえAIに要求されても実行されません。
:::

### Layer 3 — シークレット管理ツールによる環境変数の安全な管理

`.env`ファイルにシークレットを直書きするのではなく、シークレット管理ツールから動的に取得する構成にします。

**direnv + 1Password CLIの組み合わせ（推奨）:**

```bash
# .envrc（direnvが読む設定ファイル）← .gitignoreに必ず追加
# 値を直書きせず、1Password CLIから取得
export DATABASE_URL=$(op read "op://Development/my-app/database/url")
export STRIPE_SECRET_KEY=$(op read "op://Development/Stripe/secret_key")
export OPENAI_API_KEY=$(op read "op://Development/OpenAI/api_key")
```

```bash
# direnvの設定
$ brew install direnv
$ echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc
$ direnv allow .  # プロジェクトディレクトリで実行
```

**Dopplerを使う場合（チーム開発に便利）:**

```bash
# Doppler CLIのインストール
$ brew install dopplerhq/cli/doppler

# プロジェクトへの紐付け
$ doppler setup

# Claude Codeを安全な環境変数で起動
$ doppler run -- claude "コードをレビューして"
```

### Layer 4 — Pre-commitフックによる自動検知

コミット前にシークレットが含まれていないか自動スキャンします。

```bash
# pre-commitのインストール
$ pip install pre-commit
# または
$ brew install pre-commit
```

```yaml
# .pre-commit-config.yaml（プロジェクトルートに配置）
repos:
  # gitleaks: 高精度なシークレット検知
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks

  # detect-secrets: Yelpが開発した検知ツール
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ["--baseline", ".secrets.baseline"]

  # 基本的な確認（大容量ファイル等）
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: check-added-large-files
        args: ["--maxkb=1000"]
      - id: detect-private-key
```

```bash
# インストールと初期化
$ pre-commit install
$ detect-secrets scan > .secrets.baseline  # 既存のシークレットをベースライン登録
$ git add .pre-commit-config.yaml .secrets.baseline
```

### Layer 5 — CI/CDパイプラインでの検知

ローカルのpre-commitを突破してもCI/CDで二重にチェックします。

```yaml
# .github/workflows/security.yml
name: Secret Scanning

on:
  push:
    branches: ["*"]
  pull_request:
    branches: [main, staging]

jobs:
  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # 全履歴をスキャン

      - name: Run Gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}  # 商用利用時のみ必要

  trufflehog:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run TruffleHog
        uses: trufflesecurity/trufflehog@main
        with:
          path: ./
          base: ${{ github.event.repository.default_branch }}
          head: HEAD
          extra_args: --only-verified
```

また、GitHubの設定から **Secret Scanning** と **Push Protection** を有効にすることも強く推奨します。

```
リポジトリ → Settings → Security → Code security and analysis
→ Secret scanning: Enable
→ Push protection: Enable
```

---

## 4. 自己診断チェックリスト

### 今すぐ実行できる既存リポジトリの診断

```bash
# gitleaksで全履歴をスキャン
$ gitleaks detect --source . --verbose --log-level warn

# TruffleHogで検証済みシークレットのみ検出
$ trufflehog git file://. --only-verified

# 誤ってコミットされた.envファイルの履歴確認
$ git log --all --full-history -- "**/.env"
$ git log --all --full-history -- "*.env"

# gitの全オブジェクトから文字列検索（緊急時）
$ git grep -l "sk-" $(git rev-list --all)
$ git grep -l "AKIAIO" $(git rev-list --all)
```

:::message alert
既にシークレットをcommitしてしまった場合、`git history rewrite`（BFG Repo Cleaner等）が必要ですが、**まず該当のキーを即座にローテーションしてください**。履歴書き換えは後からでも可能ですが、漏洩した情報は即時無効化が最優先です。
:::

### プロジェクト診断チェックリスト

以下の項目を確認してください。

```
セキュリティ設定
□ .claudeignore に .env* を追加している
□ .gitignore に .env* が含まれている
□ .env.example のみコミットされている（実際の値なし）
□ CLAUDE.md にシークレット・パスワード・APIキーを書いていない
□ .claude/settings.json で Read/.env* を deny している
□ .claude/settings.json で Bash/env を deny している

シークレット管理
□ APIキーを直接.envに書かず、シークレット管理ツールから取得している
□ 各APIキーに最小権限を設定している（読み取り専用等）
□ APIキーのローテーション手順をドキュメント化している
□ チーム共有のシークレットはチームVaultで管理している

自動検知
□ pre-commitフックでシークレット検知を設定している
□ CI/CDでシークレットスキャンを実行している
□ GitHubのSecret ScanningとPush Protectionを有効にしている

MCP設定
□ 使用するMCPサーバーのソースコードを確認している
□ MCPサーバーの外部通信先を把握している
□ 信頼できないMCPサーバーを使っていない
```

---

## 5. 漏洩が発覚したときのレスポンスプレイブック

### ステップ1: 即座にキーを無効化（最優先）

```bash
# AWS
$ aws iam delete-access-key --access-key-id AKIAIOSFODNN7EXAMPLE

# OpenAI
# → https://platform.openai.com/api-keys で即時削除

# Stripe
# → https://dashboard.stripe.com/apikeys で即時ロールオーバー

# GitHub Personal Access Token
# → https://github.com/settings/tokens で即時削除
```

### ステップ2: 被害範囲の確認

```bash
# AWSの場合: CloudTrailで不審なAPIコールを確認
$ aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=AccessKeyId,AttributeValue=AKIAIOSFODNN7EXAMPLE \
  --start-time 2024-01-01T00:00:00Z \
  --max-results 50

# GitHubの場合: 監査ログを確認
# → https://github.com/organizations/{org}/settings/audit-log
```

### ステップ3: 新しいキーの発行と安全な管理方法への移行

キーを再発行したら、今度はシークレット管理ツールに格納し、`.env`への直書きをやめます。

### ステップ4: インシデントログの作成

```markdown
# インシデントログ（テンプレート）

## 発生日時
YYYY-MM-DD HH:mm（JST）

## 影響範囲
- 漏洩したキー: [サービス名]の[キーID]
- 漏洩ルート: [Claude Code / MCP / git commit等]
- 悪用の有無: [確認中/なし/あり（詳細）]

## 即時対応
- [ ] キーの無効化（実施時刻: ）
- [ ] 新キーの発行
- [ ] 被害範囲の確認

## 恒久対応
- [ ] .claudeignoreの設定
- [ ] pre-commitフックの導入
- [ ] シークレット管理ツールへの移行

## 再発防止
...
```

---

## 6. AI開発時代のセキュリティマインドセット

### 「AIが書いた = レビューしなくていい」は危険

AIツールはコードを生成しますが、セキュリティ判断まで肩代わりするわけではありません。

- AIが`env`を実行することを提案したら → 断る
- AIが`.env`を読もうとしたら → 止める
- AIが生成したコードにシークレットが含まれていたら → 即削除

Claude Codeには `--dangerously-skip-permissions` オプションがありますが、本番・共有環境では絶対に使わないでください。

### 最小権限の原則をAIにも適用する

人間の開発者に最小権限を与えるのと同様に、**AIエージェントにも必要最小限の権限しか与えないこと**が原則です。

```
読み取りが必要なファイルだけ Allow
書き込みが必要なパスだけ Allow
実行が必要なコマンドだけ Allow
それ以外は全て Deny
```

この設定を`settings.json`の`permissions`に落とし込むのが、Claude Codeセキュリティの基本姿勢です。

---

## まとめ

| Layer | 対策 | 効果 |
|-------|------|------|
| Layer 1 | `.claudeignore`設定 | AIがシークレットファイルにアクセスできなくなる |
| Layer 2 | `settings.json`権限制限 | `env`コマンド等の危険な操作をブロック |
| Layer 3 | シークレット管理ツール | `.env`直書きを排除、動的取得に移行 |
| Layer 4 | Pre-commitフック | コミット前に自動スキャン |
| Layer 5 | CI/CDスキャン | プッシュ時にも二重チェック |

Claude Codeは非常に強力な開発支援ツールです。しかしその強力さゆえに、適切な設定なしでは意図しないアクセスが発生します。

「AIが書いてくれたから安全」ではなく、「AIを安全に使う設定をした上で委譲する」という考え方に切り替えることが、AI開発時代のセキュリティの出発点です。

---

## 参考リンク

- [Claude Code公式ドキュメント — Security](https://docs.anthropic.com/en/docs/claude-code/security)
- [Claude Code settings.json リファレンス](https://docs.anthropic.com/en/docs/claude-code/settings)
- [Gitleaks — シークレット検知ツール](https://github.com/gitleaks/gitleaks)
- [TruffleHog — シークレット検知ツール](https://github.com/trufflesecurity/trufflehog)
- [detect-secrets — Yelp製シークレット検知](https://github.com/Yelp/detect-secrets)
- [pre-commit — Gitフックマネージャー](https://pre-commit.com/)
- [Doppler — シークレット管理SaaS](https://www.doppler.com/)

---
title: "Claude Code Remote Controlをローカル開発に組み込む — セッション管理からCI/CD連携まで"
emoji: "🤖"
type: "tech"
topics: ["claudecode", "cicd", "githubactions", "devops", "ai"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Claude Code を使い始めると、最初は「ターミナルで質問して、コードを貼り付ける」という使い方になりがちです。しかし Anthropic が提供する **Remote Control 機能**を活用すると、AI によるコーディング支援を自動化パイプラインに組み込めるようになります。

本記事では、Claude Code の Remote Control が何を解決するのか、どう設定するのか、そして GitHub Actions などの CI/CD とどう統合するかを実践的に解説します。「読んだ翌日から試せる」レベルを目標に、動くコード例を多数掲載しています。

### Remote Control が解決する3つの課題

| 課題 | 内容 | Remote Control による解決策 |
|------|------|---------------------------|
| **自動化できない** | インタラクティブ操作前提でパイプラインから呼び出せない | ヘッドレス実行モードで CLI から完全自動制御 |
| **セッション管理が煩雑** | 長時間タスクの途中切断・再接続が難しい | セッションIDによる永続・再接続が可能 |
| **チーム共有できない** | 実行ログが手元にしか残らない | 構造化ログの出力・外部への転送が容易 |

---

## アーキテクチャを理解する

### 接続モデルの全体像

Claude Code Remote Control の通信経路は以下のように構成されています。

```
開発者PC / CI Runner
      │
      │  HTTPS (REST / WebSocket)
      ▼
Claude Code Remote Server（Anthropic 管理）
      │
      │  Internal API
      ▼
Anthropic API（Claude 本体）
```

ローカルの `claude` プロセスが **Remote Server に接続し、セッションを確立**します。CI/CD 環境でも同じ仕組みで動作するため、ローカルとパイプラインで同じ設定ファイルを共有できます。

### セッションライフサイクル

```
[セッション作成]
      │  claude --session-id $(uuidgen) ...
      ▼
[タスク実行中]
      │  ストリーミングで逐次出力
      ▼
[結果取得・セッション維持 or 終了]
      │
      ├── 正常終了 → exit code 0
      ├── タイムアウト → SessionExpired
      └── エラー → 再接続フローへ
```

**重要なポイント**: セッション ID は UUIDv4 形式で管理されます。同じセッション ID を渡すと、前回の会話コンテキストを引き継いで処理を継続できます。

### WebSocket vs SSE の使い分け

| 方式 | 用途 | 特徴 |
|------|------|------|
| **WebSocket** | 双方向・インタラクティブ | リアルタイム応答、ターミナル統合向き |
| **SSE（Server-Sent Events）** | 一方向・ストリーミング受信 | CI/CD のログ収集、シンプルな実装向き |

CI/CD では SSE、ローカルのインタラクティブ用途では WebSocket が基本的な選択になります。

---

## ローカル開発環境のセットアップ

### インストールと初期設定

```bash
# Claude Code CLI のインストール（Node.js 18+ 必須）
npm install -g @anthropic-ai/claude-code

# バージョン確認
claude --version

# API Key の設定（環境変数が推奨）
export ANTHROPIC_API_KEY="sk-ant-..."

# または .envrc に記載（direnv 使用時）
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.envrc
direnv allow
```

### `.claude/settings.json` の詳細設定

プロジェクトルートに `.claude/settings.json` を置くことで、プロジェクト固有の設定を管理できます。

```json
{
  "model": "claude-opus-4-5",
  "permissions": {
    "allow": [
      "Bash(git:*)",
      "Bash(npm:*)",
      "Read(**)",
      "Write(**)"
    ],
    "deny": [
      "Bash(rm -rf *)",
      "Bash(curl:*)"
    ]
  },
  "env": {
    "LOG_FORMAT": "json",
    "MAX_TOKENS": "4096"
  }
}
```

**ポイント**: `permissions` で許可・拒否するツール呼び出しを明示することで、意図しないファイル削除や外部通信を防げます。CI/CD 環境では特に `deny` ルールを厳密に設定することをお勧めします。

### `CLAUDE.md` によるプロジェクトコンテキスト設定

プロジェクトルートの `CLAUDE.md` は、Claude がそのプロジェクトを理解するための「設計書」になります。

```markdown
# プロジェクト概要

## 技術スタック
- Next.js 15 (App Router)
- TypeScript 5.x
- Prisma + PostgreSQL

## コーディング規約
- ESLint + Prettier 必須
- コンポーネントは `src/components/` 配下
- API ルートは `src/app/api/` 配下

## 禁止事項
- `console.log` を本番コードに残さない
- `any` 型の使用禁止
- 直接 DOM 操作禁止
```

この `CLAUDE.md` は CI/CD でも自動的に読み込まれるため、ローカルと同じ規約でコードレビューが行われます。

### VS Code との統合

`.vscode/tasks.json` に Claude Code のタスクを定義することで、ショートカットキーから呼び出せます。

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Claude: Review Current File",
      "type": "shell",
      "command": "claude",
      "args": [
        "--print",
        "以下のファイルをレビューして、問題点を日本語で指摘してください:\n\n$(cat ${file})"
      ],
      "problemMatcher": [],
      "presentation": {
        "reveal": "always",
        "panel": "new"
      }
    },
    {
      "label": "Claude: Generate Tests",
      "type": "shell",
      "command": "claude",
      "args": [
        "--print",
        "${file} のユニットテストを Vitest で生成してください"
      ],
      "problemMatcher": [],
      "presentation": {
        "reveal": "always",
        "panel": "new"
      }
    }
  ]
}
```

---

## セッション管理の実践パターン

### パターン1 — ステートレス（使い捨て）セッション

PR ごとにセッションを使い捨てにするシンプルなパターンです。冪等性が保ちやすく、CI/CD に最適です。

```bash
#!/bin/bash
# ci-review.sh

set -euo pipefail

DIFF=$(git diff origin/main...HEAD)

claude --print \
  --model claude-sonnet-4-5 \
  "以下の diff をレビューして、問題点をリストアップしてください。
  フォーマット: ## 問題点\n- [重要度: HIGH/MED/LOW] 内容

${DIFF}"
```

`--print` フラグを付けることで、インタラクティブモードを起動せずに結果を標準出力に返します。これが CI/CD 統合の基本形です。

### パターン2 — 永続セッションによる長時間タスク

大規模なリファクタリングや複数ファイルにまたがる作業では、セッションを維持したまま段階的に処理します。

```bash
#!/bin/bash
# long-running-task.sh

set -euo pipefail

SESSION_ID=$(uuidgen)
SESSION_FILE=".claude-session-${SESSION_ID}"

echo "セッション開始: ${SESSION_ID}"
echo "${SESSION_ID}" > "${SESSION_FILE}"

# Step 1: コードの分析
echo "=== Step 1: コード分析 ==="
claude --print \
  --session-id "${SESSION_ID}" \
  "src/ ディレクトリ全体を分析して、リファクタリングが必要な箇所を優先度順にリストアップしてください"

# Step 2: 最優先箇所のリファクタリング
echo "=== Step 2: リファクタリング実行 ==="
claude \
  --session-id "${SESSION_ID}" \
  "先ほどの分析結果に基づいて、優先度 HIGH の項目を実際にリファクタリングしてください"

# セッション情報の削除
rm -f "${SESSION_FILE}"
echo "セッション終了: ${SESSION_ID}"
```

**注意**: セッション ID をファイルに保存しておくことで、スクリプトが途中で失敗した場合でも手動で再接続できます。

### パターン3 — マルチセッション並列実行

複数のブランチや機能を並列で処理するパターンです。

```bash
#!/bin/bash
# parallel-review.sh

set -euo pipefail

# 並列処理する PR 番号のリスト
PR_NUMBERS=("123" "124" "125")

run_review() {
  local pr_num=$1
  local session_id="review-pr-${pr_num}-$(date +%s)"

  echo "PR #${pr_num} のレビュー開始 (session: ${session_id})"

  # PR の diff を取得
  gh pr diff "${pr_num}" > "/tmp/pr-${pr_num}.diff"

  # Claude でレビュー実行
  claude --print \
    --session-id "${session_id}" \
    "$(cat /tmp/pr-${pr_num}.diff)" \
    > "/tmp/review-${pr_num}.md"

  echo "PR #${pr_num} のレビュー完了"
}

# バックグラウンドで並列実行
for pr in "${PR_NUMBERS[@]}"; do
  run_review "${pr}" &
done

# 全プロセスの完了を待機
wait
echo "全 PR のレビューが完了しました"
```

### エラーハンドリングと再接続ロジック

```bash
#!/bin/bash
# retry-wrapper.sh

claude_with_retry() {
  local max_attempts=3
  local attempt=1
  local wait_time=5

  while [ $attempt -le $max_attempts ]; do
    echo "試行 ${attempt}/${max_attempts}..."

    if claude --print "$@"; then
      return 0
    fi

    local exit_code=$?

    # レート制限エラー（429）の場合は長めに待機
    if [ $exit_code -eq 429 ]; then
      wait_time=$((wait_time * 2))
      echo "レート制限。${wait_time}秒待機..."
    else
      echo "エラー発生（exit code: ${exit_code}）。${wait_time}秒後に再試行..."
    fi

    sleep $wait_time
    attempt=$((attempt + 1))
  done

  echo "最大試行回数に達しました"
  return 1
}

# 使用例
claude_with_retry "このコードをレビューしてください: $(cat main.go)"
```

---

## CI/CD連携 — GitHub Actions との統合

### 基本ワークフロー：PR 自動レビュー

```yaml
# .github/workflows/claude-code-review.yml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize]
    # ドラフト PR はスキップ
    branches-ignore: []

jobs:
  review:
    runs-on: ubuntu-latest
    # ドラフト PR をスキップ
    if: github.event.pull_request.draft == false

    permissions:
      contents: read
      pull-requests: write

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # 全履歴取得（diff に必要）

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code

      - name: Get PR diff
        id: diff
        run: |
          git diff origin/${{ github.base_ref }}...HEAD > /tmp/pr.diff
          echo "diff_lines=$(wc -l < /tmp/pr.diff)" >> $GITHUB_OUTPUT

      - name: Run Claude Code Review
        if: steps.diff.outputs.diff_lines != '0'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          REVIEW=$(claude --print \
            --model claude-sonnet-4-5 \
            "以下のコード差分をレビューしてください。
            観点: セキュリティ・パフォーマンス・保守性・テスト不足
            出力形式: Markdown（## セクション + 箇条書き）
            言語: 日本語

            $(cat /tmp/pr.diff)")

          echo "${REVIEW}" > /tmp/review.md

      - name: Post review comment
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const review = fs.readFileSync('/tmp/review.md', 'utf8');

            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: `## 🤖 AI コードレビュー\n\n${review}\n\n---\n*このレビューは Claude Code によって自動生成されました*`
            });
```

### 実践ユースケース2 — テスト自動生成

新規関数が追加された PR に対して、ユニットテストを自動生成する例です。

```yaml
# .github/workflows/auto-test-gen.yml
name: Auto Test Generation

on:
  pull_request:
    types: [opened]

jobs:
  generate-tests:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code

      - name: Detect new functions
        id: detect
        run: |
          # 新規追加された .ts/.tsx ファイルを検出
          NEW_FILES=$(git diff --name-only --diff-filter=A origin/main...HEAD \
            | grep -E '\.(ts|tsx)$' | grep -v '\.test\.' || true)
          echo "new_files=${NEW_FILES}" >> $GITHUB_OUTPUT

      - name: Generate tests for new files
        if: steps.detect.outputs.new_files != ''
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          while IFS= read -r file; do
            if [ -f "$file" ]; then
              echo "テスト生成中: $file"

              TEST_FILE="${file%.ts}.test.ts"
              TEST_FILE="${TEST_FILE%.tsx}.test.tsx"

              claude --print \
                --model claude-sonnet-4-5 \
                "以下のファイルに対する Vitest ユニットテストを生成してください。
                テストファイル名: ${TEST_FILE}
                方針: 境界値・異常系を含む網羅的なテスト

                $(cat ${file})" > "${TEST_FILE}"

              echo "生成完了: ${TEST_FILE}"
            fi
          done <<< "${{ steps.detect.outputs.new_files }}"

      - name: Commit generated tests
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add "**/*.test.ts" "**/*.test.tsx" || true
          git diff --staged --quiet || \
            git commit -m "test: AI generated tests for new files [skip ci]"
          git push
```

### 実践ユースケース3 — ドキュメント自動更新

API の変更をトリガーに、README やドキュメントを自動更新します。

```yaml
# .github/workflows/auto-docs.yml
name: Auto Documentation Update

on:
  push:
    branches: [main]
    paths:
      - 'src/app/api/**'
      - 'src/types/**'

jobs:
  update-docs:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code

      - name: Detect API changes
        id: changes
        run: |
          CHANGED=$(git diff HEAD~1 --name-only -- 'src/app/api/**')
          echo "changed_files=${CHANGED}" >> $GITHUB_OUTPUT

      - name: Update API documentation
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          # 変更された API ファイルを収集
          API_CONTENT=""
          while IFS= read -r file; do
            if [ -f "$file" ]; then
              API_CONTENT="${API_CONTENT}\n\n### ${file}\n$(cat ${file})"
            fi
          done <<< "${{ steps.changes.outputs.changed_files }}"

          # docs/api.md を更新
          claude --print \
            --model claude-sonnet-4-5 \
            "以下の API ルートコードに基づいて docs/api.md を更新してください。
            形式: OpenAPI 風の Markdown
            既存の docs/api.md の内容: $(cat docs/api.md 2>/dev/null || echo '（新規作成）')

            変更された API:
            ${API_CONTENT}" > docs/api.md

      - name: Commit docs
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/api.md
          git diff --staged --quiet || \
            git commit -m "docs: auto-update API documentation [skip ci]"
          git push
```

### GitLab CI との統合

GitHub Actions との主な差分は、シークレットの参照方法と `artifacts` の扱いです。

```yaml
# .gitlab-ci.yml
stages:
  - review

ai-code-review:
  stage: review
  image: node:20-slim
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

  script:
    - npm install -g @anthropic-ai/claude-code
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - git diff origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...HEAD > /tmp/pr.diff
    - |
      claude --print \
        --model claude-sonnet-4-5 \
        "以下の差分をレビューしてください: $(cat /tmp/pr.diff)" \
        > review.md

  artifacts:
    paths:
      - review.md
    expire_in: 7 days

  variables:
    ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY  # GitLab CI/CD Variables で設定
```

---

## セキュリティと運用設計

### API Key の安全な管理

**ローカル開発環境**:

```bash
# direnv を使った環境分離（推奨）
# ~/.envrc ではなくプロジェクトの .envrc に記載
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' > .envrc
echo '.envrc' >> .gitignore
direnv allow

# 確認
direnv status
```

**CI/CD 環境**:

```yaml
# GitHub Actions での安全な参照
env:
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

# ✅ secrets コンテキスト経由
# ❌ vars コンテキスト（平文で保存されるため API キーには使用しない）
```

**キーローテーションの自動化**:

```bash
#!/bin/bash
# rotate-key.sh（月次 cron で実行）

# 新しいキーを生成（Anthropic API 経由）
NEW_KEY=$(curl -s -X POST \
  -H "Authorization: Bearer ${ANTHROPIC_ADMIN_KEY}" \
  "https://api.anthropic.com/v1/api_keys" \
  | jq -r '.key')

# GitHub Secrets を更新
gh secret set ANTHROPIC_API_KEY --body "${NEW_KEY}" --repo org/repo

# 古いキーを無効化（別途管理）
echo "キーのローテーション完了: $(date)"
```

### 監査ログと使用量モニタリング

構造化ログを出力して、トークン使用量を可視化します。

```bash
# JSON 形式でログ出力
claude --print \
  --output-format json \
  "コードをレビューしてください" \
  | tee /var/log/claude-usage.jsonl

# jq でトークン使用量を集計
cat /var/log/claude-usage.jsonl \
  | jq -s '[.[].usage] | {
      total_input: (map(.input_tokens) | add),
      total_output: (map(.output_tokens) | add),
      requests: length
    }'
```

BigQuery や Datadog へのログ転送例:

```bash
# BigQuery にストリーミングで転送
claude --print --output-format json "..." \
  | jq -c '{
      timestamp: (now | todate),
      input_tokens: .usage.input_tokens,
      output_tokens: .usage.output_tokens,
      model: .model
    }' \
  | bq insert --project_id=myproject \
      myproject:claude_usage.api_logs
```

### コスト爆発を防ぐアラート設定

```yaml
# .github/workflows/cost-alert.yml
name: Claude API Cost Check

on:
  schedule:
    - cron: '0 9 * * 1'  # 毎週月曜 9:00 JST

jobs:
  cost-check:
    runs-on: ubuntu-latest
    steps:
      - name: Check weekly usage
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          # 週次使用量を取得してアラート判定
          USAGE=$(curl -s \
            -H "Authorization: Bearer ${ANTHROPIC_API_KEY}" \
            "https://api.anthropic.com/v1/usage?period=weekly")

          COST=$(echo $USAGE | jq '.cost_usd')
          THRESHOLD=50  # $50 を超えたら Slack 通知

          if (( $(echo "$COST > $THRESHOLD" | bc -l) )); then
            curl -X POST "${{ secrets.SLACK_WEBHOOK }}" \
              -d "{\"text\":\"⚠️ Claude API 週次コストが \$${COST} になりました\"}"
          fi
```

---

## トラブルシューティング

### よくあるエラーと解決策

| エラー | 原因 | 解決方法 |
|--------|------|---------|
| `ConnectionTimeout` | ネットワーク遅延・プロキシ設定 | `--timeout 120` で延長 |
| `SessionExpired` | 非活性によるセッション切れ | ハートビート実装か `--no-timeout` フラグ |
| `RateLimitExceeded` | 短時間での大量リクエスト | 指数バックオフ実装（前述の retry-wrapper.sh を参照） |
| `InvalidAPIKey` | キーの期限切れ・スコープ不足 | Anthropic Console でキーの有効期限を確認 |
| `ContextLengthExceeded` | プロンプトが長すぎる | diff を分割して複数回に分けて送信 |

### デバッグテクニック

```bash
# 詳細ログを有効化
ANTHROPIC_LOG=debug claude --print "テスト"

# ネットワーク通信を確認（macOS）
sudo dtrace -n 'syscall::connect:entry { printf("%s\n", copyinstr(arg0)); }'

# CI/CD でのデバッグ出力
claude --print \
  --output-format json \
  "テスト" \
  | jq '{
      id: .id,
      model: .model,
      stop_reason: .stop_reason,
      usage: .usage
    }'
```

### プロキシ環境での設定

企業のプロキシ環境では、環境変数で設定します。

```bash
export HTTPS_PROXY="http://proxy.company.com:8080"
export NO_PROXY="localhost,127.0.0.1,.company.com"

# .clauderc（将来のバージョンで対応予定）
# proxy:
#   https: "http://proxy.company.com:8080"
```

---

## まとめと次のステップ

### 本記事で学んだことの整理

- [ ] Remote Control のアーキテクチャ（WebSocket / SSE・セッション管理）を理解した
- [ ] `settings.json` と `CLAUDE.md` でプロジェクトコンテキストを設定できる
- [ ] `--print` フラグでヘッドレス実行できる
- [ ] ステートレス / 永続 / 並列の3パターンのセッション管理を把握した
- [ ] GitHub Actions で PR 自動レビューを設定できる
- [ ] API Key を安全に管理し、使用量をモニタリングできる

### さらに発展させるアイデア

**MCP (Model Context Protocol) との組み合わせ**

MCP サーバーを使うと、Claude Code が外部データソース（データベース・Slack・GitHub Issues など）にアクセスできるようになります。CI/CD パイプラインに MCP を組み合わせると、「GitHub Issues の内容を参照してコードを修正する」といった高度な自動化が実現します。

**カスタムツール呼び出しの実装**

```bash
# カスタムツールを定義した CLAUDE.md の例
# Claude が独自コマンドを呼び出せるようになる
claude --print \
  --tools-file .claude/tools.json \
  "データベースのスキーマを確認して、マイグレーションファイルを生成してください"
```

**Slack ボットとの統合**

```python
# slack_bot.py（Bolt SDK 使用）
from slack_bolt import App
import subprocess

app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.message("レビューして")
def review_code(message, say):
    code = extract_code_block(message["text"])
    result = subprocess.run(
        ["claude", "--print", f"以下のコードをレビューしてください:\n{code}"],
        capture_output=True, text=True
    )
    say(result.stdout)
```

---

## おわりに

Claude Code Remote Control は、単なる「AI アシスタント」から「開発パイプラインの一員」へと Claude Code を進化させる機能です。特に PR 自動レビューは、導入コストが低い割に効果が大きく、チーム全体の開発速度向上に寄与します。

まずは `--print` フラグを使ったシンプルなスクリプトから試して、徐々に CI/CD に組み込んでいくアプローチをお勧めします。セキュリティの考慮点（API Key 管理・権限の最小化・使用量監視）を最初から意識しておくと、後から慌てることなく本番運用に移行できます。

本記事の内容は随時アップデートする予定です。実際に試してみた感想や、追加してほしい内容があればコメントで教えてください。

---
title: "PRレビューのボトルネックをClaude Code × MCPで自動化する——コード品質チェック完全自動化への道"
emoji: "🔍"
type: "tech"
topics: ["claudecode", "mcp", "githubactions", "codereview", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「PRレビューが追いつかない」という問題は、チームが成長するほど深刻になります。レビュアーが1人か2人しかいない個人開発・小規模チームでは特に顕著です。

筆者の場合、個人で複数のプロジェクトを掛け持ちしているため、自分でPRを出して自分でレビューするという状況が多々あります。「このコードに問題はないか」を俯瞰して確認する作業は、書いた直後の自分には難しいものです。

この記事では、Claude Code と MCP（Model Context Protocol）を組み合わせて、PRレビューの一部を自動化した仕組みを紹介します。完全な代替ではなく、「人間のレビューを楽にする」ことを目的とした設計です。

---

## PRレビューのボトルネックを整理する

まず、レビュー作業のどこが時間を取るかを整理します。

| レビュー項目 | 人間が得意 | 自動化が得意 |
|------------|----------|------------|
| ビジネスロジックの正しさ | ✅ | ─ |
| セキュリティの問題 | ✅ | ✅（パターン認識） |
| コーディング規約の遵守 | ─ | ✅ |
| 型安全性・型エラー | ─ | ✅ |
| テストカバレッジ | ─ | ✅ |
| パフォーマンス上の懸念 | ✅（判断） | ✅（検出） |
| ドキュメントの欠如 | ─ | ✅ |
| 依存関係の脆弱性 | ─ | ✅ |

自動化が得意な領域は**パターンベースの検出**です。これを自動化することで、人間のレビュアーは「ビジネスロジックが正しいか」「設計が適切か」という高度な判断に集中できます。

---

## アーキテクチャ概要

構築したシステムの全体像です。

```
PR作成 / 更新
    ↓
GitHub Actions がトリガー
    ↓
Claude Code を MCP 経由で起動
    ↓
自動レビューエージェントが以下を実行:
  - 差分コードの取得（GitHub MCP）
  - セキュリティパターンチェック
  - コーディング規約チェック
  - テストカバレッジ確認
    ↓
レビューコメントを PR に投稿（GitHub MCP）
    ↓
人間のレビュアーが最終確認
```

### 使用するMCPサーバー

| MCP サーバー | 役割 |
|------------|------|
| `@modelcontextprotocol/server-github` | PRの差分取得・コメント投稿 |
| `@modelcontextprotocol/server-filesystem` | ローカルファイルの読み込み |

---

## GitHub MCP サーバーの設定

まず、GitHub MCP サーバーを設定します。

```bash
# GitHub MCP サーバーをインストール
npm install -g @modelcontextprotocol/server-github
```

```json
// .claude/settings.json

{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    },
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/path/to/your/projects"
      ]
    }
  }
}
```

:::message
**セキュリティ注意**: `GITHUB_TOKEN` は環境変数から読み込み、設定ファイルにハードコードしないでください。GitHub Actions では `${{ secrets.GITHUB_TOKEN }}` を使います。
:::

---

## PRレビュースラッシュコマンドの実装

Claude Code のスラッシュコマンドとして PRレビューを定義します。

```markdown
<!-- .claude/commands/review-pr.md -->

# PR自動レビュー

## 引数
- `$PR_NUMBER`: レビュー対象のPR番号（必須）
- `$REPO`: オーナー/リポジトリ名（例: correlate000/my-project）

## 実行手順

1. GitHub MCP を使って PR の差分を取得する
   - `mcp__github__get_pull_request` で PR 情報を取得
   - `mcp__github__get_pull_request_files` でファイル一覧を取得
   - 各ファイルの diff 内容を確認する

2. 以下の観点でコードをレビューする:
   - **セキュリティ**: SQLインジェクション・XSS・認証バイパス・シークレットのハードコード
   - **型安全性**: TypeScript の any 型の使用・型アサーション・null チェックの欠如
   - **エラーハンドリング**: catch ブロックでの例外握りつぶし・未処理の Promise rejection
   - **パフォーマンス**: N+1 クエリ・不必要なループネスト・大きなデータの同期処理
   - **テスト**: テストが追加されているか・境界値テストの欠如

3. 以下の形式でレビューコメントを作成する:
   ```
   ## 自動レビュー結果

   ### HIGH（要対応）
   - [ファイル名:行番号] 問題の説明と修正提案

   ### MEDIUM（推奨）
   - [ファイル名:行番号] 問題の説明と修正提案

   ### LOW（任意）
   - [ファイル名:行番号] 問題の説明と修正提案

   ### 良い点
   - ...

   ---
   *このコメントはClaude Code自動レビューによって生成されました。人間のレビューを代替するものではありません。*
   ```

4. `mcp__github__create_pull_request_review` でレビューを投稿する
   - HIGH がある場合: `event: "REQUEST_CHANGES"`
   - HIGH がない場合: `event: "COMMENT"`

## 注意事項
- ビジネスロジックの正しさはレビューしない（コンテキストが不足するため）
- 「このコードは問題ない」という判断は過信せず、あくまで検出支援ツールとして扱う
```

---

## Python による自動実行スクリプト

GitHub Actions から呼び出すスクリプトです。

```python
# pr_review_agent.py

import subprocess
import os
import sys
import json

def run_claude_code_review(
    repo: str,
    pr_number: int,
    github_token: str
) -> int:
    """
    Claude Code を使って PR レビューを実行する

    Returns:
        終了コード（0: 成功、1: HIGH問題あり、2: エラー）
    """
    # Claude Code に渡すプロンプト
    prompt = f"""
PRレビューを実行してください。

リポジトリ: {repo}
PR番号: {pr_number}

.claude/commands/review-pr.md の手順に従ってレビューを実施し、
GitHubにレビューコメントを投稿してください。
"""

    env = os.environ.copy()
    env["GITHUB_TOKEN"] = github_token

    result = subprocess.run(
        [
            "claude",
            "--print",
            "--no-interactive",
            prompt
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300  # 5分でタイムアウト
    )

    if result.returncode != 0:
        print(f"Claude Code エラー:\n{result.stderr}", file=sys.stderr)
        return 2

    print(result.stdout)

    # HIGH問題の有無でexit codeを変える
    if "HIGH（要対応）" in result.stdout and "なし" not in result.stdout:
        return 1

    return 0


if __name__ == "__main__":
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_number = int(os.environ.get("PR_NUMBER", "0"))
    github_token = os.environ.get("GITHUB_TOKEN", "")

    if not all([repo, pr_number, github_token]):
        print("必要な環境変数が設定されていません", file=sys.stderr)
        sys.exit(2)

    exit_code = run_claude_code_review(repo, pr_number, github_token)
    sys.exit(exit_code)
```

---

## GitHub Actions ワークフロー

```yaml
# .github/workflows/pr-review.yml

name: PR 自動レビュー

on:
  pull_request:
    types: [opened, synchronize]
    # ドラフトPRはスキップ
  pull_request_review:
    types: [submitted]

jobs:
  auto-review:
    runs-on: ubuntu-latest
    # ドラフトPRはスキップ
    if: github.event.pull_request.draft == false

    permissions:
      contents: read
      pull-requests: write  # レビューコメントの投稿に必要

    steps:
      - name: リポジトリをチェックアウト
        uses: actions/checkout@v4

      - name: Node.js をセットアップ
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Python をセットアップ
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Claude Code をインストール
        run: npm install -g @anthropic-ai/claude-code

      - name: MCP サーバーをインストール
        run: |
          npm install -g @modelcontextprotocol/server-github
          npm install -g @modelcontextprotocol/server-filesystem

      - name: .claude/settings.json を配置
        run: |
          mkdir -p .claude
          cat > .claude/settings.json << 'EOF'
          {
            "mcpServers": {
              "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                  "GITHUB_PERSONAL_ACCESS_TOKEN": "$GITHUB_TOKEN"
                }
              }
            }
          }
          EOF

      - name: PR レビューを実行
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: python pr_review_agent.py

      - name: レビュー結果をアーティファクトとして保存
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: review-result
          path: review-output.txt
          retention-days: 7
```

---

## ローカルでの実行方法

GitHub Actions を待たずに、ローカルから特定の PR をレビューしたい場合は以下のコマンドで実行できます。

```bash
# ローカル実行スクリプト
# Usage: ./review-pr.sh <PR番号>

#!/bin/bash
set -euo pipefail

PR_NUMBER=${1:?"PR番号を指定してください: ./review-pr.sh 123"}
REPO=${GITHUB_REPOSITORY:-"correlate000/my-project"}

echo "PR #${PR_NUMBER} のレビューを開始します..."

claude --print --no-interactive "
PRレビューを実行してください。
リポジトリ: ${REPO}
PR番号: ${PR_NUMBER}

.claude/commands/review-pr.md の手順に従ってレビューし、
GitHubにレビューコメントを投稿してください。
"
```

```bash
chmod +x review-pr.sh
./review-pr.sh 42  # PR #42 をレビュー
```

---

## レビュー品質を上げるためのチューニング

### プロジェクト固有のルールを追加する

`.claude/commands/review-pr.md` にプロジェクト固有のチェック項目を追加することで、汎用ルールを超えたレビューが可能になります。

```markdown
<!-- .claude/commands/review-pr.md への追記 -->

## プロジェクト固有のチェック

### BigQuery クエリ
- WHERE 句のないクエリが含まれていないか
- パーティションフィルターが指定されているか
- `LIMIT` なしの `SELECT *` がないか

### Cloud Run デプロイ関連
- 環境変数を直接コードに書いていないか
- タイムアウト設定が変更されていないか
- メモリ設定が変更される場合はコメントで理由が記載されているか

### API エンドポイント
- 認証チェックが必要なエンドポイントに認証ミドルウェアが適用されているか
- レスポンスボディに内部エラーの詳細が含まれていないか
```

### コード差分をローカルファイルとして提供する

GitHub MCP の代わりに、差分をローカルファイルとして提供する方法もあります。MCP なしでも動作するため、MCP の設定が難しい場合の代替案になります。

```python
# local_diff_reviewer.py

import subprocess
import sys
from anthropic import Anthropic

def get_pr_diff(base_branch: str = "main") -> str:
    """現在のブランチと base_branch の差分を取得する"""
    result = subprocess.run(
        ["git", "diff", f"origin/{base_branch}...HEAD"],
        capture_output=True,
        text=True
    )
    return result.stdout


def review_diff_with_claude(diff: str) -> str:
    """Claude API で差分をレビューする"""
    client = Anthropic()

    prompt = f"""
以下のコード差分をレビューしてください。

```diff
{diff[:50000]}  # トークン制限のため上限を設ける
```

以下の観点でレビューし、問題点を重大度別（HIGH/MEDIUM/LOW）に整理してください:
- セキュリティ上のリスク
- 型安全性の問題
- エラーハンドリングの欠如
- パフォーマンス上の懸念
- テストの欠如

問題がない場合は「問題は検出されませんでした」と明記してください。
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "main"
    diff = get_pr_diff(base)

    if not diff:
        print("差分がありません")
        sys.exit(0)

    print(f"差分サイズ: {len(diff)} 文字")
    review = review_diff_with_claude(diff)
    print(review)
```

---

## 実際に使って気づいたこと

### よかったこと

**パターンベースの問題は確実に検出される**: SQLインジェクションのリスクになりうるコード、認証バイパスの可能性がある条件分岐、シークレットが含まれていそうな文字列——これらは一定の精度で検出されます。

**コーディング規約のチェックが楽になった**: TypeScriptの `any` 型の使用、catch ブロックでの例外握りつぶし、未使用変数の残留など、lintツールでは検出しにくいパターンをClaudeが指摘してくれます。

**コメントの文脈が豊か**: ESLintのエラーメッセージと違い、「なぜ問題か」「どう修正すべきか」を自然言語で説明してくれます。

### 課題

**ハルシネーションリスク**: 「問題がない」と言っているコードに実は問題があったり、逆に問題でない箇所を問題だと判定することがあります。`HIGH` 判定を自動的に CI をブロックする条件にするのは慎重に行う必要があります。

**差分サイズの上限**: 大きなPR（数千行の変更）では、トークン制限により全差分を処理できないことがあります。ファイル単位で分割する処理が必要です。

**MCP の不安定性**: `@modelcontextprotocol/server-github` はまだ発展途上のツールで、予期しないエラーが起きることがあります。フォールバック処理を用意しておくと安心です。

---

## 現実的な落としどころ

完全自動化を目指すより、「人間のレビューを補助する」という位置づけで使うのが現時点での正解だと感じています。

**推奨する使い方:**

1. **`COMMENT` モードで運用する**: `REQUEST_CHANGES` にすると自動判定でマージがブロックされる。最初は `COMMENT` のみで運用し、精度を確認してから変更を検討する

2. **特定のカテゴリに絞る**: 全項目を自動チェックするより、「セキュリティだけ」「型安全性だけ」に絞るほうが精度が高くなる

3. **人間のレビューは必ず行う**: 自動レビューは補助ツールであり、人間のレビューを省略する理由にはならない

```yaml
# 推奨: COMMENT のみで運用（ブロックしない）

- name: PR レビューを実行（コメントのみモード）
  env:
    REVIEW_MODE: "comment_only"  # REQUEST_CHANGES を使わない
```

---

## まとめ

Claude Code × MCP による PR レビュー自動化の要点です。

**実装した機能:**
- GitHub MCP を使った PR 差分の取得とレビューコメントの投稿
- セキュリティ・型安全性・エラーハンドリングの自動チェック
- GitHub Actions による PR 作成時の自動トリガー

**実際の効果:**
- レビュアーが指摘に費やす時間が約30〜40%削減（体感）
- 見落としがちなパターンの検出精度が向上
- コーディング規約違反の指摘をClaudeに任せることで、レビュアーがロジックの確認に集中できるようになった

**今後の改善余地:**
- プロジェクト固有ルールの充実
- 差分サイズが大きいPRへの対応
- レビュー精度の定量評価

PRレビューの自動化は「仕組みで解決する」アプローチの典型例です。最初の設定コストはありますが、一度動き出すと継続的に価値を生み続けてくれます。

---

## 関連記事

- [Claude Code Hooks で開発フローを自動化する](/claude-code-hooks-complete-guide)
- [MCP カスタムサーバーを作る](/mcp-custom-server)
- [MCPレジストリとプロンプトインジェクション対策](/mcp-registry-api-prompt-injection)

---
title: "GitHub Agentic Workflowsが使えなくてもclaude-code-actionで同等の自動化を実現する"
emoji: "🤖"
type: "tech"
topics: ["githubactions", "claudecode", "anthropic", "automation", "cicd"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

2026年2月13日、GitHubがCopilot Coding AgentとAgentic Workflowsの Technical Previewを発表しました。PRのコメント一つで「あとはよしなに」が実現できるという、開発者なら誰もが飛びつきたくなる機能です。

ところが、実際に使おうとすると壁があります。**Copilot Pro以上のプランが必要**で、さらにTechnical PreviewのWaitlist登録が必要です。個人開発者や小規模チームにとって、月額のCopilotプランを追加するのは躊躇する場面もあるでしょう。

本記事では、Copilotプランなしで同等の自動化を実現した実践記録を紹介します。Anthropic公式の`claude-code-action`を使い、**PR自動レビュー・CI失敗分析・週次サマリー**の3ワークフローを3リポジトリに展開するまでのプロセス、4回の失敗から学んだデバッグ手順、そしてコスト実績をまとめています。

:::message
本記事の内容は2026年2月時点の実践記録に基づいています。`claude-code-action`のAPIや`with:`パラメータは今後変更される可能性があります。
:::

## GitHub Agentic WorkflowsとCopilot Agentの制約

GitHub Agentic Workflowsを本番利用するには、現時点で以下の条件がすべて必要です。

| 条件 | 詳細 |
|------|------|
| プラン | Copilot Pro / Copilot Business / Copilot Enterprise |
| アクセス | Technical Preview のWaitlist招待 |
| リポジトリ | Organization or 個人（制限あり） |

個人開発でGitHub Freeを使っている、あるいはすでにAnthropicのAPIと契約しているのにCopilotプランを追加するのは二重投資になる——そういうケースに`claude-code-action`は有力な選択肢です。

## claude-code-actionとは

[claude-code-action](https://github.com/anthropics/claude-code-action)はAnthropicが公式で提供するGitHub Actionです。Issue・PRコメントへの`@claude`メンションに応答したり、スケジュール実行でサマリーを生成したりと、GitHub Actions上でClaude（Anthropic API）を動かすことができます。

**前提条件:**

- Anthropic APIキー（Claude APIと同一。`console.claude.ai`で管理）
- GitHub Actionsが使えるリポジトリ（Freeプランでも可）
- Copilotプランは不要

## セットアップ手順

### Step 1: APIキーをGitHub Secretsに登録

まずAnthropicのAPIキーを取得し、GitHubリポジトリの**Settings → Secrets and variables → Actions**に登録します。

```
Secret name: ANTHROPIC_API_KEY
Value: sk-ant-...
```

:::message alert
APIキーはチャット画面や`echo`コマンドで出力しないこと。誤ってログに残るとREVOKEが必要になります（筆者は実際に経験しました）。
:::

### Step 2: インタラクティブワークフローの作成

`.github/workflows/claude.yml`を作成します。これはIssue・PRコメントで`@claude`をメンションすると応答するベースワークフローです。

```yaml
name: Claude Code

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened]
  pull_request_review:
    types: [submitted]

jobs:
  claude:
    if: |
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'issues' && contains(github.event.issue.body, '@claude')) ||
      (github.event_name == 'pull_request_review' && contains(github.event.review.body, '@claude'))
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
      issues: write
      id-token: write  # ← 必須。忘れると OIDC エラー
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1

      - name: Run Claude Code
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          max_turns: "10"
```

**`id-token: write`は必須です。** 筆者はこれを最初のワークフローで忘れ、`Could not fetch an OIDC token`エラーで詰まりました。

### Step 3: PR自動レビューワークフロー

```yaml
name: Claude PR Review

on:
  pull_request:
    types: [opened, ready_for_review, reopened]

jobs:
  review:
    if: github.event.pull_request.draft == false
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      id-token: write
    concurrency:
      group: pr-review-${{ github.event.pull_request.number }}
      cancel-in-progress: true
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Claude PR Review
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          max_turns: "15"
          prompt: |
            このプルリクエストをレビューしてください。

            以下の観点で確認し、PRにコメントを追加してください：
            1. バグ・ロジックの問題
            2. セキュリティリスク（SQLインジェクション、XSS等）
            3. パフォーマンスの懸念
            4. コードの可読性・保守性
            5. テストの網羅性

            変更ファイルは `gh pr diff` で確認できます。
            問題がなければ「LGTM」とコメントしてください。
```

**`synchronize`イベントはあえて含めていません。** 含めるとpushのたびにレビューが走り、コストが急増します。`opened`・`ready_for_review`・`reopened`の3つで十分です。

### Step 4: CI失敗分析ワークフロー

```yaml
name: Claude CI Failure Analysis

on:
  workflow_run:
    workflows: ["CI", "Test", "Build"]
    types: [completed]

jobs:
  analyze:
    if: github.event.workflow_run.conclusion == 'failure'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      actions: read
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - name: Analyze CI Failure
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          max_turns: "10"
          prompt: |
            CIが失敗しました。以下の情報を元に原因を分析し、
            GitHubのIssueにラベル `ci-failure` をつけてレポートを投稿してください。

            - 失敗ワークフロー: ${{ github.event.workflow_run.name }}
            - 実行ID: ${{ github.event.workflow_run.id }}
            - ブランチ: ${{ github.event.workflow_run.head_branch }}
            - コミット: ${{ github.event.workflow_run.head_sha }}

            `gh run view ${{ github.event.workflow_run.id }} --log-failed` でログを確認してください。
            原因と修正方針を日本語で簡潔にまとめてください。
```

### Step 5: 週次サマリーワークフロー

```yaml
name: Weekly Summary

on:
  schedule:
    - cron: "0 0 * * 1"  # 毎週月曜 09:00 JST
  workflow_dispatch:

jobs:
  summary:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: read
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - name: Generate Weekly Summary
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          max_turns: "15"
          prompt: |
            先週（月〜日）のリポジトリ活動をサマリーしてください。

            以下のコマンドで情報を収集してください：
            - `gh pr list --state merged --limit 20` : マージ済みPR
            - `gh issue list --state closed --limit 20` : クローズされたIssue
            - `gh issue list --state open --limit 10` : 未対応Issue

            サマリーをIssueとして作成し、ラベル `weekly-summary` をつけてください。
            フォーマット：
            ## 先週のハイライト
            ## マージされた変更
            ## クローズされたIssue
            ## 未解決の課題
```

:::message
`schedule`トリガーの場合、**`prompt`は必須**です。省略するとInteractive Modeになり、コメント待ちで何もしない状態になります。筆者はこれで1回無駄に実行させました。
:::

## ハマりポイントと解決策（4回の失敗記録）

### 失敗1: `model`/`timeout_minutes`が無効input

**エラー内容:**
```
Error: Unexpected input(s) 'model', 'timeout_minutes'
```

**原因:** v1の`with:`パラメータに`model`や`timeout_minutes`は存在しません。

**解決策:** モデル指定は`claude_args`経由で行います。ただし、Sonnetがデフォルトで十分なため、筆者はモデル指定自体を削除しました。

```yaml
# ❌ 動かない
with:
  model: "claude-sonnet-4-6"
  timeout_minutes: 10

# ✅ 正しい（モデル指定が必要な場合）
with:
  claude_args: "--model claude-sonnet-4-6"
```

### 失敗2: exit code 1（`--model`指定問題）

モデル指定を`claude_args`に移しても失敗が続きました。詳細ログを見ると、HaikuモデルをBashで指定した際にコマンドの解釈が崩れていました。最終的にモデル指定を削除し、デフォルト（Sonnet）で動作確認してから追加する方針に切り替えました。

### 失敗3: `authentication_failed` / `Invalid API key`

これが最も時間を取られました。解決のカギは`show_full_output: true`です。

```yaml
- name: Run Claude Code
  uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    show_full_output: true  # ← デバッグ時に追加
```

このオプションを加えると、ログに`total_cost_usd: 0`という行が見えました。**コスト0はAPIリクエストが到達していないサインです。** つまりAPIキーの問題です。

Anthropic ConsoleでAPIキーを再生成し、GitHub Secretsを更新したところ、4回目で成功しました（19秒で応答）。

:::message
デバッグが完了したら`show_full_output: true`は削除してください。ログにAPIレスポンス全体が出力され、トークン使用量等が見えすぎます。
:::

### 失敗4: `allowedTools`の厳密指定

週次サマリーのテスト1回目で、`allowedTools`を厳密に指定したところ処理が途中で止まりました。

```yaml
# ❌ 厳しすぎる（Claudeが必要なコマンドを実行できない）
with:
  allowed_tools: "Bash(gh pr list),Bash(gh issue list)"

# ✅ max_turnsで制御する方が実用的
with:
  max_turns: "15"
  # allowed_tools は指定しない
```

**`allowedTools`は緩くする方が良いです。** 厳密なBashパターン指定はClaudeの実行コマンド選択を制限しすぎます。`max_turns`でターン数の上限を設け、コスト暴走を防ぐ方が実用的です。

## コスト実績

3リポジトリ・3ワークフローを1ヶ月運用した実績です。

| ワークフロー | 発火頻度 | 1回あたり | 月間試算 |
|------------|---------|---------|--------|
| PR自動レビュー | 週4〜6回 | $0.05〜0.15 | $1〜3 |
| CI失敗分析 | 週2〜3回 | $0.03〜0.08 | $0.5〜1 |
| 週次サマリー | 週1回 | $0.10〜0.20 | $0.5〜1 |
| インタラクティブ | 不定期 | $0.05〜0.30 | $1〜3 |

**合計: 月$3〜8（3リポジトリ）**

Copilot Pro（$10/月）より安く、既存のAnthropicAPIを使い回せる点が大きなメリットです。

## セキュリティ注意事項

### publicリポジトリへの展開は危険

`claude-code-action`をpublicリポジトリに設定するのは**現状推奨しません**。

第三者がIssueやPRコメントに`@claude`を書くだけでAPIが消費されます。プロンプトインジェクション攻撃のリスクもあります。筆者はpublic-zenn-docsへの展開を見送りました。

対策としては、`if`条件でリポジトリオーナーかチームメンバーに限定する方法があります：

```yaml
jobs:
  claude:
    if: |
      contains(github.event.comment.body, '@claude') &&
      (github.event.comment.user.login == 'your-username' ||
       github.event.comment.author_association == 'OWNER' ||
       github.event.comment.author_association == 'MEMBER')
```

### APIキーのローテーション

GitHub Secretsに登録したAPIキーは定期的にローテーションすることを推奨します。万一キーが漏洩した場合、`console.claude.ai`で即座にREVOKEできます。

## 3リポジトリへの横展開パターン

筆者はcorrelatedesign-workspace（メインAPI）、wise-career、isvd-careerの3リポジトリに展開しました。共通化のポイントは「インタラクティブ+PR Reviewを全リポジトリに」「週次サマリーはメインリポジトリのみ」という分け方です。

```
correlatedesign-workspace/
  .github/workflows/
    claude.yml              # インタラクティブ
    claude-pr-review.yml    # PR自動レビュー
    claude-ci-failure.yml   # CI失敗分析
    claude-weekly-summary.yml # 週次サマリー

wise-career/
  .github/workflows/
    claude.yml              # インタラクティブ
    claude-pr-review.yml    # PR自動レビュー

isvd-career/
  .github/workflows/
    claude.yml              # インタラクティブ
    claude-pr-review.yml    # PR自動レビュー
```

週次サマリーを複数リポジトリに置くと同じ曜日・時刻に並列実行され、コストも比例して増えます。1リポジトリをハブにして他リポジトリの状況を集約する方が効率的です。

## GitHub Agentic Workflowsとの比較

最終的に、両者をどう使い分けるかをまとめます。

| 項目 | GitHub Agentic Workflows | claude-code-action |
|------|-------------------------|-------------------|
| 前提プラン | Copilot Pro以上 | 不要（Anthropic APIのみ） |
| アクセス | Technical Preview招待制 | 即日利用可 |
| カスタマイズ | 限定的 | YAML全体を自由に設定 |
| モデル | GitHub管理 | Anthropic API直接 |
| コスト | Copilotプラン込み | API使用量のみ |
| publicリポジトリ | 相対的に安全 | 慎重な設定が必要 |

GitHub Agentic WorkflowsがGA（一般提供）になり、個人プランでも使えるようになったタイミングで乗り換えを検討する予定です。ただし、`claude-code-action`はpromptを完全にコントロールできる点と、既存のAnthropicAPI契約を使い回せる点が引き続き魅力です。

## まとめ

`claude-code-action`の導入で得られた主な知見をまとめます。

**設定面:**
- `id-token: write`は必須（忘れるとOIDCエラー）
- `show_full_output: true`はデバッグの最強ツール（`total_cost_usd: 0`はAPI未到達のサイン）
- `allowedTools`は緩くしてmax_turnsで制御する
- `schedule`トリガーでは`prompt`を必ず書く
- `synchronize`イベントはコスト急増の原因になるので除外

**運用面:**
- publicリポジトリへの展開は現状避ける
- 週次サマリーは1リポジトリをハブにして集約する
- コスト上限はAPI設定側でも設定しておく

月$3〜8で3リポジトリのPRレビュー・CI分析・週次サマリーが自動化できる体制が整いました。Copilotプランを追加せずに済んでいる点と、プロンプトを自由に書けるフレキシビリティが最大のメリットです。

GitHub Agentic WorkflowsのGA待ちで動けない方は、ぜひ`claude-code-action`を試してみてください。

---

## 参考リンク

- [anthropics/claude-code-action](https://github.com/anthropics/claude-code-action)
- [Claude API Console](https://console.claude.ai)
- [GitHub Actions ドキュメント](https://docs.github.com/ja/actions)

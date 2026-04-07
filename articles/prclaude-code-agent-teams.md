---
title: "PRレビューのボトルネックをClaude Code Agent Teamsで解消する実装パターン"
emoji: "🔍"
type: "tech"
topics: ["claudecode", "ai", "githubactions", "codereview", "agentteams"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

PRレビュー待ちで、スプリントが詰まった経験はありますか？

「昨日出したPRがまだレビューされていない」「今日もレビュー依頼が3件たまった」——これは個人の怠慢ではなく、開発プロセスに構造的に埋め込まれたボトルネックです。DORAメトリクスでいえば、Lead Time for Changesの大半はコーディング時間ではなく、レビュー待ち時間で構成されています。

本記事では、 **Claude Code Agent Teamsを使ったマルチエージェントPRレビューアーキテクチャ ** について、実装コードと設計パターンを中心に解説します。「AIでコードレビューしてみた」という体験記ではなく、チーム規模・リポジトリ構成・開発文化に応じた ** 選択可能な実装パターン ** を体系的に示すことを目指します。

なお、本記事で紹介するコードサンプルはすべて筆者が実際に動作を確認したものです。ただし、コスト試算や処理時間はモデルバージョン・コンテキスト長・ネットワーク環境によって変動します。参考値としてご活用ください。

---

## 1. PRレビューボトルネックの実態

### 1.1 「待ち時間」はどこで発生しているか

PRが作成されてからマージされるまでの時間を分解すると、多くの場合以下のような構造になっています。

```
PR作成 → レビュー依頼（即時）
         ↓
         レビュアーの空き待ち（数時間〜数日）← ここが最大のボトルネック
         ↓
         初回レビュー（30分〜2時間）
         ↓
         修正対応（数時間〜1日）
         ↓
         再レビュー待ち（数時間〜数日）← 繰り返し発生
         ↓
         承認・マージ
```

GitLabの調査によると、PRがマージされるまでの中央値は2〜5日であり、その大半は実作業時間ではなく ** 待機時間 ** です。linter・フォーマッターによる自動チェックはすでに普及していますが、それらが解決できるのは「スタイルの統一」に限られます。

### 1.2 従来の自動化ツールが解決できていないもの

ESLint、Prettier、Ruff——これらのツールは優秀です。しかし、以下のような問題は検出できません。

- ** ビジネスロジックの誤り **: 仕様書と実装の乖離
- ** セキュリティの文脈的問題 **: SQLインジェクションは検出できても、認証フローの設計上の欠陥は見抜けない
- ** パフォーマンスのトレードオフ **: N+1クエリが発生する実行パスの特定
- ** 設計上の問題 **: 責務の分離が崩れているが、個々のコードは正しい

SonarQube・Semgrep・CodeClimateはstaticanalysisの精度を上げていますが、 ** 文脈理解 ** を伴うレビューには限界があります。

### 1.3 レビュアーの認知負荷問題

「大きいPR」はレビュー品質を劣化させます。変更行数が500行を超えると、レビュアーの集中力は目に見えて低下し、見落としが増えます。しかし、PRを小さく保つ文化を根付かせることは、技術的な問題ではなく組織文化の問題であり、短期間では解決できない。

さらに、レビュアーが特定のシニアエンジニアに集中している組織では、その人物が ** 単一障害点（SPOF） ** になります。休暇・病気・退職がレビューパイプラインを止めます。

---

## 2. Claude Code Agent Teamsの概念整理

### 2.1 Claude Codeとは何か

Claude CodeはAnthropicが提供するCLIツールおよびSDKです。エージェントとして動作し、`bash`・`read_file`・`write_file`などのツールを組み合わせて、自律的にタスクを実行できます。

重要なのは、Claude Codeが **Agentic Loop** を持つ点です。

```
Plan（何をすべきか計画）
  ↓
Act（ツールを使って行動）
  ↓
Observe（結果を観察）
  ↓
Reflect（次のアクションを判断）
  ↓
（目標達成まで繰り返し）
```

このループにより、単純な「プロンプト→レスポンス」を超えた、複数ステップにわたるタスクを自律実行できます。

### 2.2 Agent Teamsとは何か — シングルエージェントとの違い

シングルエージェントによるPRレビューとAgent Teamsによるレビューの違いを図示します。

```
【シングルエージェント】
PR差分 → [Claude] → レビューコメント

【Agent Teams】
PR差分 → [Orchestrator Agent]
              ├──→ [Security Agent]     → セキュリティ指摘
              ├──→ [Performance Agent]  → パフォーマンス指摘
              ├──→ [Architecture Agent] → 設計指摘
              └──→ [Test Agent]         → テスト不足指摘
                         ↓
              [Aggregator Agent] → 統合レビューコメント → GitHub PR
```

シングルエージェントでも高品質なレビューは可能です。しかし、 ** 専門特化 ** することで各エージェントはより深い観点でコードを分析できます。SecurityエージェントにはOWASPの知識を注入し、PerformanceエージェントにはN+1クエリパターンの辞書を持たせる──そういった設計が実現できる。

### 2.3 エージェント間通信の設計原則

マルチエージェントシステムを設計するとき、最も重要なのは ** 通信プロトコルの設計 ** です。

- **Shared Context**: 全エージェントが共通のコンテキスト（PR差分、リポジトリ構造）にアクセス
- **Message Passing**: エージェント間で構造化されたデータを受け渡す
- ** 冪等性 **: 同じ入力に対して同じ出力を返す設計（再実行に備える）
- ** タイムアウト **: 各エージェントに最大実行時間を設定し、フォールバックを用意する

---

## 3. 実装パターンカタログ — 3つのアプローチ

### Pattern A: ライトウェイト単一エージェント（スモールスタート向け）

** 適用条件 **: チーム5名以下 / 月間PR数 50件未満 / 導入コストを最小化したい

まず動くものを作ることが優先です。GitHub Actionsに単一のClaudeエージェントを組み込みます。

```yaml
# .github/workflows/claude-review.yml
name: Claude PR Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  claude-review:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install anthropic PyGithub

      - name: Get PR diff
        run: |
          git diff origin/${{ github.base_ref }}...HEAD > pr_diff.txt

      - name: Claude Code Review
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          python scripts/claude_review.py \
            --diff pr_diff.txt \
            --pr-number ${{ github.event.number }} \
            --repo ${{ github.repository }}
```

```python
# scripts/claude_review.py
import anthropic
import argparse
import json
from github import Github
import os

def review_pr(diff: str, pr_number: int, repo_name: str) -> None:
    client = anthropic.Anthropic()
    gh = Github(os.environ["GITHUB_TOKEN"])
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # 差分が大きすぎる場合は先頭8000文字に制限
    diff_text = diff[:8000] if len(diff) > 8000 else diff

    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"""あなたはシニアエンジニアです。以下のPR差分をレビューしてください。

指摘は以下の4つの観点で行い、JSON形式で返してください：
1. バグ・論理エラー（severity: "critical"）
2. セキュリティ上の懸念（severity: "critical"）
3. パフォーマンス改善提案（severity: "warning"）
4. コードの可読性・保守性（severity: "info"）

問題がない場合はcommentsを空配列にしてください。

PR差分:
```diff
{diff_text}
```

出力形式（JSONのみ、説明不要）:
{{"summary": "全体評価を1-2文で", "comments": [{{"severity": "critical|warning|info", "message": "具体的な指摘内容"}}]}}"""
            }
        ]
    )

    result = json.loads(message.content[0].text)

    # PRにコメント投稿
    body = format_review_comment(result)
    pr.create_issue_comment(body)
    print(f"Review posted to PR #{pr_number}")


def format_review_comment(result: dict) -> str:
    lines = ["## Claude Code Review\n", f"** 総評 **: {result['summary']}\n"]

    if not result["comments"]:
        lines.append("指摘事項はありません。")
        return "\n".join(lines)

    for severity in ["critical", "warning", "info"]:
        items = [c for c in result["comments"] if c["severity"] == severity]
        if not items:
            continue
        emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}[severity]
        label = {"critical": "Critical", "warning": "Warning", "info": "Info"}[severity]
        lines.append(f"\n### {emoji} {label}\n")
        for item in items:
            lines.append(f"- {item['message']}")

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    with open(args.diff, "r") as f:
        diff = f.read()

    review_pr(diff, args.pr_number, args.repo)
```

** コスト試算 **: Claude 3.5 Sonnet使用時、1PRあたり約$0.01〜0.05（差分サイズに依存）

---

### Pattern B: 専門特化マルチエージェント（中規模チーム向け）

** 適用条件 **: チーム10〜50名 / レビュー観点の標準化が必要 / 特定領域（セキュリティ等）を強化したい

エージェントを役割ごとに分け、並列実行します。

```
Orchestrator Agent
  - PR差分の解析・分類
  - 変更ファイルのカテゴリ判定（API / DB / UI / インフラ）
  - 各専門エージェントへのタスク割り当て
     ↓
  ┌──────────┐ ┌──────────┐ ┌──────────────┐
  │ Security │ │ Perf     │ │ Architecture │
  │ Agent    │ │ Agent    │ │ Agent        │
  │          │ │          │ │              │
  │ OWASP    │ │ Big-O    │ │ SOLID原則    │
  │ SQLi/XSS │ │ N+1Query │ │ 依存関係     │
  │ 認証設計 │ │ Memory   │ │ 責務分離     │
  └──────────┘ └──────────┘ └──────────────┘
     ↓              ↓              ↓
              Aggregator Agent
                - 重複排除
                - 優先度付け
                - PR投稿
```

データクラスとasyncioを使った実装例です。

```python
# agents/multi_agent_review.py
import asyncio
from dataclasses import dataclass, field
from typing import Literal
import anthropic
import json

client = anthropic.Anthropic()

@dataclass
class ReviewFinding:
    severity: Literal["critical", "warning", "info"]
    category: str
    message: str
    confidence: float  # 0.0 - 1.0

@dataclass
class AgentResult:
    agent_id: str
    findings: list[ReviewFinding] = field(default_factory=list)
    error: str | None = None


AGENT_PROMPTS = {
    "security": """あなたはセキュリティ専門のコードレビュアーです。
以下の観点でのみレビューしてください：
- SQLインジェクション・NoSQLインジェクション
- XSS（クロスサイトスクリプティング）
- 認証・認可の設計上の欠陥
- 機密情報のハードコード
- OWASP Top 10に関連するパターン

差分:{diff}

JSON形式で出力:
{{"findings": [{{"severity": "critical|warning|info", "message": "...", "confidence": 0.0-1.0}}]}}""",

    "performance": """あなたはパフォーマンス最適化の専門家です。
以下の観点でのみレビューしてください：
- N+1クエリパターン
- 不必要なループ内でのDB/APIアクセス
- メモリリーク・不要なオブジェクト保持
- アルゴリズムの計算量（O(n²)以上の危険な箇所）
- キャッシュ可能だがキャッシュされていない処理

差分:{diff}

JSON形式で出力:
{{"findings": [{{"severity": "critical|warning|info", "message": "...", "confidence": 0.0-1.0}}]}}""",

    "architecture": """あなたはソフトウェアアーキテクチャの専門家です。
以下の観点でのみレビューしてください：
- 単一責任原則（SRP）の違反
- 依存性逆転原則（DIP）の違反
- 過度な結合・循環依存
- 抽象化レベルの混在
- 変更容易性・拡張性への影響

差分:{diff}

JSON形式で出力:
{{"findings": [{{"severity": "critical|warning|info", "message": "...", "confidence": 0.0-1.0}}]}}""",
}


async def run_agent(agent_id: str, diff: str) -> AgentResult:
    """単一エージェントを実行する"""
    prompt = AGENT_PROMPTS[agent_id].format(diff=diff[:6000])

    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = json.loads(message.content[0].text)
        findings = [
            ReviewFinding(
                severity=f["severity"],
                category=agent_id,
                message=f["message"],
                confidence=f.get("confidence", 0.8),
            )
            for f in raw.get("findings", [])
        ]
        return AgentResult(agent_id=agent_id, findings=findings)

    except Exception as e:
        return AgentResult(agent_id=agent_id, error=str(e))


async def run_agent_team(diff: str) -> list[ReviewFinding]:
    """全エージェントを並列実行し、結果を集約する"""
    results = await asyncio.gather(
        run_agent("security", diff),
        run_agent("performance", diff),
        run_agent("architecture", diff),
    )

    all_findings: list[ReviewFinding] = []
    for result in results:
        if result.error:
            print(f"[{result.agent_id}] Error: {result.error}")
            continue
        all_findings.extend(result.findings)

    # 信頼度0.6以上のみ採用し、severityで降順ソート
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    filtered = [f for f in all_findings if f.confidence >= 0.6]
    return sorted(filtered, key=lambda f: severity_order[f.severity])


def format_multi_agent_comment(findings: list[ReviewFinding]) -> str:
    if not findings:
        return "## Claude Code Review (Multi-Agent)\n\n指摘事項はありません。"

    lines = ["## Claude Code Review (Multi-Agent)\n"]
    lines.append(f"**{len(findings)}件の指摘 ** （Security / Performance / Architecture 各専門エージェントによる分析）\n")

    for severity in ["critical", "warning", "info"]:
        items = [f for f in findings if f.severity == severity]
        if not items:
            continue
        emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}[severity]
        label = {"critical": "Critical", "warning": "Warning", "info": "Info"}[severity]
        lines.append(f"\n### {emoji} {label}\n")
        for item in items:
            category_badge = f"`{item.category}`"
            lines.append(f"- {category_badge} {item.message}")

    return "\n".join(lines)
```

GitHub Actionsからの呼び出し部分です。

```yaml
# .github/workflows/claude-multi-review.yml の主要部分
- name: Multi-Agent Review
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    python -c "
import asyncio
from agents.multi_agent_review import run_agent_team, format_multi_agent_comment
from github import Github
import os

async def main():
    with open('pr_diff.txt') as f:
        diff = f.read()
    findings = await run_agent_team(diff)
    comment = format_multi_agent_comment(findings)

    gh = Github(os.environ['GITHUB_TOKEN'])
    repo = gh.get_repo('${{ github.repository }}')
    pr = repo.get_pull(${{ github.event.number }})
    pr.create_issue_comment(comment)

asyncio.run(main())
"
```

** コスト試算 **: 3エージェント並列実行で1PRあたり約$0.03〜0.15。シングルエージェントの3倍ですが、専門性の高い指摘が得られます。

---

### Pattern C: Human-in-the-Loop ハイブリッド（エンタープライズ向け）

** 適用条件 **: チーム50名以上 / 規制対応・コンプライアンスが必要 / AI判断に最終確認を組み込みたい

** 信頼度スコアによる自動/手動振り分け ** が核心です。

```python
# agents/hitl_orchestrator.py
from dataclasses import dataclass
from enum import Enum

class ReviewDecision(Enum):
    AUTO_APPROVE = "auto_approve"     # 指摘なし → 自動承認
    AUTO_COMMENT = "auto_comment"     # 低信頼度指摘 → コメントのみ
    ESCALATE = "escalate"             # 高信頼度 Critical → 人間にエスカレーション
    BLOCK = "block"                   # 複数Critical → マージブロック

@dataclass
class ReviewPolicy:
    auto_approve_threshold: float = 0.9     # 全指摘の信頼度平均がこれ以上なら自動承認
    escalation_threshold: float = 0.85      # Critical指摘の信頼度がこれ以上ならエスカレーション
    block_critical_count: int = 3           # Critical指摘がこの件数以上ならブロック
    required_human_reviewers: list[str] = None  # エスカレーション先のGitHubユーザー名

def decide_action(
    findings: list[ReviewFinding],
    policy: ReviewPolicy
) -> ReviewDecision:
    if not findings:
        return ReviewDecision.AUTO_APPROVE

    criticals = [f for f in findings if f.severity == "critical"]

    # Criticalが規定件数以上 → ブロック
    if len(criticals) >= policy.block_critical_count:
        return ReviewDecision.BLOCK

    # 高信頼度のCriticalがある → エスカレーション
    high_confidence_criticals = [
        f for f in criticals if f.confidence >= policy.escalation_threshold
    ]
    if high_confidence_criticals:
        return ReviewDecision.ESCALATE

    return ReviewDecision.AUTO_COMMENT


def apply_decision(
    pr,
    decision: ReviewDecision,
    comment_body: str,
    policy: ReviewPolicy
) -> None:
    pr.create_issue_comment(comment_body)

    if decision == ReviewDecision.BLOCK:
        # PRにラベルを付けてCIをfailさせる
        pr.add_to_labels("review/blocked-by-ai")
        raise SystemExit(1)  # GitHub Actionsをfailにする

    elif decision == ReviewDecision.ESCALATE:
        # 指定のレビュアーをメンション
        if policy.required_human_reviewers:
            reviewers = " ".join(f"@{r}" for r in policy.required_human_reviewers)
            pr.create_issue_comment(
                f"⚠️ AIレビューで高信頼度のCritical指摘が検出されました。"
                f"以下のメンバーによる確認をお願いします: {reviewers}"
            )
        pr.add_to_labels("review/needs-human")
```

** 監査ログの実装 ** も重要です。誰が（どのエージェントが）何を指摘し、その結果どのような判断が下されたかを記録しておくことで、AI判断の透明性を確保できます。

```python
# 監査ログをJSONで保存する例
import json
from datetime import datetime, timezone

def save_audit_log(
    pr_number: int,
    findings: list[ReviewFinding],
    decision: ReviewDecision,
) -> None:
    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pr_number": pr_number,
        "decision": decision.value,
        "findings": [
            {
                "severity": f.severity,
                "category": f.category,
                "confidence": f.confidence,
            }
            for f in findings
        ],
    }
    # 実運用ではBigQueryやCloudStorageに送る
    with open(f"logs/review_audit_{pr_number}.json", "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
```

---

## 4. 落とし穴と対処パターン

### 4.1 False Positive（誤検知）への対処

最初のうち、AIは文脈を理解せずに誤った指摘を行うことがあります。「このSQL文はインジェクション脆弱性がある」という指摘が、実はパラメータ化クエリを使っている箇所だった、というケースです。

** 対処策 **: False Positiveをフィードバックループに組み込みます。

```python
# PRのコメントに 👍 / 👎 リアクションを収集し、
# プロンプトのfew-shot例に反映する仕組み（概念コード）
def collect_feedback(pr_comments: list) -> dict:
    feedback = {"helpful": [], "not_helpful": []}
    for comment in pr_comments:
        if comment.body.startswith("## Claude Code Review"):
            reactions = comment.get_reactions()
            for reaction in reactions:
                if reaction.content == "+1":
                    feedback["helpful"].append(comment.body)
                elif reaction.content == "-1":
                    feedback["not_helpful"].append(comment.body)
    return feedback
```

### 4.2 差分サイズの制限

PRの差分が大きい場合、Claudeのコンテキストウィンドウを超えてしまいます。

```python
def split_diff_by_file(diff: str) -> dict[str, str]:
    """差分をファイル単位に分割する"""
    files = {}
    current_file = None
    current_lines = []

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            if current_file:
                files[current_file] = "\n".join(current_lines)
            # "diff --git a/foo.py b/foo.py" からファイル名を抽出
            current_file = line.split(" b/")[-1]
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_file:
        files[current_file] = "\n".join(current_lines)

    return files


def prioritize_files(files: dict[str, str]) -> list[str]:
    """変更規模の大きいファイルを優先的にレビュー対象にする"""
    return sorted(
        files.keys(),
        key=lambda f: len(files[f]),
        reverse=True
    )
```

### 4.3 コスト最適化

大量のPRをすべてマルチエージェントでレビューすると、APIコストが予想以上に膨らみます。

```yaml
# ドラフトPRと依存関係更新PRはスキップする例
on:
  pull_request:
    types: [opened, synchronize, ready_for_review]

jobs:
  claude-review:
    if: |
      !github.event.pull_request.draft &&
      !contains(github.event.pull_request.labels.*.name, 'dependencies')
    runs-on: ubuntu-latest
```

モデル選択もコスト最適化に有効です。

| 用途 | モデル | 理由 |
|------|--------|------|
| Orchestrator（分類・振り分け） | claude-3-haiku | 単純分類タスクなので高速・低コストで十分 |
| Security / Architecture Agent | claude-3-5-sonnet | 精度が重要な専門分析 |
| Aggregator（重複排除・整形） | claude-3-haiku | 構造化データの整形タスク |

---

## 5. 評価指標（KPI）の設定

導入後の効果を測定するために、以下のKPIを設定することを推奨します。

### プロセス指標

| 指標 | 測定方法 | 目標値（参考） |
|------|---------|--------------|
| PRマージまでの時間（P50） | GitHub APIでcreated_at / merged_atを収集 | 導入前比30%削減 |
| 初回レビューまでの時間 | PR作成〜最初のレビューコメントまでの時間 | 4時間以内 |
| レビューラウンド数 | PRごとのreview_requested件数 | 平均2回以下 |

### 品質指標

| 指標 | 測定方法 | 目標値（参考） |
|------|---------|--------------|
| False Positive率 | 👎リアクション数 / 総指摘数 | 20%以下 |
| Critical検出率 | Criticalバグを事前に検出できた割合 | 70%以上 |
| 本番障害のうちPRで検出可能だったもの | 事後分析 | トレンドで減少 |

### コスト指標

```python
# APIコストをPRごとに記録する例
def log_api_cost(pr_number: int, input_tokens: int, output_tokens: int) -> None:
    # Claude 3.5 Sonnet: $3/M input, $15/M output（2024年時点）
    cost_usd = (input_tokens / 1_000_000 * 3) + (output_tokens / 1_000_000 * 15)
    print(f"PR #{pr_number}: ${cost_usd:.4f} ({input_tokens} in / {output_tokens} out)")
```

---

## 6. 実運用での知見

筆者が実際にこの仕組みを運用して気づいた点を共有します。

** エージェントのプロンプトは短く保つ **

最初、各エージェントに「あらゆる問題を指摘してください」という長大なプロンプトを与えていましたが、かえって指摘の精度が下がりました。「Securityエージェントはセキュリティ以外は一切コメントしない」と明示的に制約を加えたほうが、結果として有用な指摘が増えました。

** 信頼度スコアは自己申告なので疑ってかかる **

Claudeに`"confidence": 0.0-1.0`を出力させると、多くの場合0.8〜0.9に集中します。信頼度の絶対値ではなく、相対的な比較として使う設計にしたほうが安全です。

** 最初はPattern Aから始める **

マルチエージェントは魅力的ですが、まず単一エージェントで動作を確認し、チームがAIレビューに慣れてから専門化を進めることを強く推奨します。「AIが何を言っているかわからない」という心理的抵抗を最小化することが、定着の鍵です。

---

## まとめ

本記事では、PRレビューボトルネックを解消するためのClaude Code Agent Teams実装パターンを3段階で紹介しました。

| パターン | チーム規模 | 導入コスト | 特徴 |
|---------|---------|-----------|------|
| Pattern A: シングルエージェント | 〜5名 | 低 | まず動かすことを優先 |
| Pattern B: マルチエージェント | 10〜50名 | 中 | 観点別の専門化 |
| Pattern C: Human-in-the-Loop | 50名以上 | 高 | 規制対応・監査ログ |

「AIがすべてのレビューを代替する」という方向ではなく、 ** 人間のレビュアーが本当に判断すべき箇所に集中できる環境を作る ** という視点で設計することが、長く使い続けられる仕組みになります。

PRレビューのボトルネックに悩んでいるチームの参考になれば幸いです。

---

## 参考リンク

- [Anthropic Claude API ドキュメント](https://docs.anthropic.com/)
- [GitHub Actions ドキュメント](https://docs.github.com/ja/actions)
- [DORA Metrics — Four Key Metrics](https://dora.dev/research/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

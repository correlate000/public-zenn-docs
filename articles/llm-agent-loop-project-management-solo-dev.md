---
title: "LLMエージェントループで個人開発のプロジェクト管理を自動化する — ADOパターンの個人適用"
emoji: "🔁"
type: "tech"
topics: ["llm", "agent", "projectmanagement", "python", "claude"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Azure DevOps (ADO) を使った企業向けエージェントループのアーキテクチャが公開されています。5つのフェーズ（Init → Content Reconciliation → Health Monitoring → Spec Aggregation → SWE Handoff）からなるこの構造を、 **Markdown ファイル + Python スクリプトで個人開発に移植した ** 実践記録です。

個人開発ではプロジェクト管理が属人化しがちです。「今どのタスクが詰まっているか」「どの決定が未完了か」を毎回手動で確認するのは時間の無駄です。エージェントループを導入することで、 ** プロジェクトの健全性を定量的に把握し、詰まりポイントを自動検出 ** できるようになる。

---

## ADO 5フェーズの概要と個人開発への対応

| ADO フェーズ | 目的 | 個人開発での代替 |
|------------|------|----------------|
| Init | プロジェクト状態の初期化 | MEMORY.md の読み込み |
| Content Reconciliation | 実装とドキュメントの照合 | セッション記録 vs 実装ファイルの差分確認 |
| Health Monitoring | プロジェクト健全性の監視 | ヘルススコアリングスクリプト |
| Spec Aggregation | 仕様の集約・整理 | Markdown Spec ファイルの生成 |
| SWE Handoff | 実装エージェントへの引き渡し | Claude Code セッションへの Context 受け渡し |

大企業向けのADOをそのまま使う必要はありません。 ** 同じ思想を Markdown ファイルと Python スクリプトで実現できます。 **

---

## フェーズ1：Init — プロジェクト状態の読み込み

エージェントループの起動時に、現在のプロジェクト状態を構造化データとして読み込みます。

```python
# project_health/init.py
from pathlib import Path
import re
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class ProjectState:
    name: str
    decisions: list[dict] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    blocked_tasks: list[dict] = field(default_factory=list)
    open_tasks: list[dict] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.utcnow)


def load_project_state(memory_path: Path) -> ProjectState:
    """MEMORY.md からプロジェクト状態を読み込む"""
    content = memory_path.read_text(encoding="utf-8")
    state = ProjectState(name=memory_path.parent.name)

    # 決定事項を抽出
    state.decisions = _extract_decisions(content)
    # リスク項目を抽出
    state.risks = _extract_risks(content)
    # ブロックされたタスクを抽出
    state.blocked_tasks = _extract_blocked_tasks(content)
    # 未完了タスクを抽出
    state.open_tasks = _extract_open_tasks(content)

    return state


def _extract_decisions(content: str) -> list[dict]:
    """## 決定事項 セクションから決定を抽出する"""
    decisions = []
    pattern = r"- \[([ x])\] (.+)"
    in_decisions_section = False

    for line in content.split("\n"):
        if "## 決定事項" in line or "## Decisions" in line:
            in_decisions_section = True
            continue
        if line.startswith("## ") and in_decisions_section:
            break
        if in_decisions_section:
            m = re.match(pattern, line)
            if m:
                decisions.append({
                    "completed": m.group(1) == "x",
                    "text": m.group(2),
                })

    return decisions


def _extract_risks(content: str) -> list[dict]:
    """リスク項目を抽出する"""
    risks = []
    risk_pattern = r"🔴|⚠️|RISK:|リスク:"

    for line in content.split("\n"):
        if re.search(risk_pattern, line):
            risks.append({"text": line.strip(), "severity": _classify_severity(line)})

    return risks


def _classify_severity(line: str) -> str:
    if "🔴" in line or "HIGH" in line.upper():
        return "high"
    if "⚠️" in line or "MEDIUM" in line.upper():
        return "medium"
    return "low"


def _extract_blocked_tasks(content: str) -> list[dict]:
    blocked = []
    for line in content.split("\n"):
        if "BLOCKED" in line.upper() or "ブロック" in line:
            blocked.append({"text": line.strip()})
    return blocked


def _extract_open_tasks(content: str) -> list[dict]:
    open_tasks = []
    for line in content.split("\n"):
        if re.match(r"- \[ \] ", line):
            open_tasks.append({"text": line[6:].strip()})
    return open_tasks
```

---

## フェーズ2：Content Reconciliation — 実装とドキュメントの照合

「実装したと記録しているが、実際にファイルが存在するか」を確認します。ドキュメントと実装の乖離を検出するフェーズです。

```python
# project_health/reconcile.py
from pathlib import Path
import re

def reconcile_files(
    session_log: Path,
    project_root: Path,
) -> dict:
    """
    セッション記録に書かれたファイルパスが実際に存在するか照合する
    Returns: {"matched": [...], "missing": [...]}
    """
    content = session_log.read_text(encoding="utf-8")

    # セッション記録内のファイルパス言及を抽出
    # 例: `src/api/main.py`, `cloud-run/api/routes/` 等
    file_pattern = r"`([^`]+\.(py|ts|tsx|md|yaml|json|sql))`"
    mentioned_files = set(re.findall(file_pattern, content))

    result = {"matched": [], "missing": []}
    for file_path, _ in mentioned_files:
        full_path = project_root / file_path
        if full_path.exists():
            result["matched"].append(file_path)
        else:
            result["missing"].append(file_path)

    return result


def check_git_status(project_root: Path) -> dict:
    """git status で未コミットファイルを検出する"""
    import subprocess
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    uncommitted = [
        line[3:].strip()
        for line in result.stdout.split("\n")
        if line.strip()
    ]
    return {"uncommitted_files": uncommitted, "count": len(uncommitted)}
```

---

## フェーズ3：Health Monitoring — 決定論的スコアリング

プロジェクトの健全性を **LLMに頼らず決定論的なスコアで算出します ** 。

スコアリング式：

```
HealthScore = 決定完了率×0.4 + (1 - リスク率)×0.3 + (1 - ブロック率)×0.3
```

この式のポイントは「LLMを使わない」ことです。LLMに健全性を判断させると、毎回コストがかかり、結果も揺れます。シンプルな数式による安定した指標算出こそ設計の核心。

```python
# project_health/scoring.py
from dataclasses import dataclass

@dataclass
class HealthScore:
    decision_completion_rate: float  # 0.0 - 1.0
    risk_rate: float                 # 0.0 - 1.0
    block_rate: float                # 0.0 - 1.0
    total_score: float               # 0.0 - 1.0
    grade: str                       # A / B / C / D


def calculate_health_score(state: "ProjectState") -> HealthScore:
    """プロジェクトヘルススコアを計算する"""
    decisions = state.decisions
    if decisions:
        completed = sum(1 for d in decisions if d["completed"])
        decision_completion_rate = completed / len(decisions)
    else:
        decision_completion_rate = 1.0  # 決定事項なし = 問題なし

    # リスク率（高リスク項目の割合）
    high_risks = sum(1 for r in state.risks if r["severity"] == "high")
    risk_rate = min(high_risks / 5, 1.0)  # 5件以上で最大

    # ブロック率（ブロックされたタスクの割合）
    total_tasks = len(state.open_tasks) + len(state.blocked_tasks)
    if total_tasks > 0:
        block_rate = len(state.blocked_tasks) / total_tasks
    else:
        block_rate = 0.0

    # 総合スコア
    total = (
        decision_completion_rate * 0.4
        + (1 - risk_rate) * 0.3
        + (1 - block_rate) * 0.3
    )

    grade = _score_to_grade(total)

    return HealthScore(
        decision_completion_rate=decision_completion_rate,
        risk_rate=risk_rate,
        block_rate=block_rate,
        total_score=total,
        grade=grade,
    )


def _score_to_grade(score: float) -> str:
    if score >= 0.9:
        return "A"
    if score >= 0.8:
        return "A-"
    if score >= 0.7:
        return "B+"
    if score >= 0.6:
        return "B"
    if score >= 0.5:
        return "C"
    return "D"


def format_health_report(state: "ProjectState", score: HealthScore) -> str:
    """ヘルスレポートを Markdown で出力する"""
    return f"""## プロジェクトヘルスレポート

** プロジェクト **: {state.name}
** 総合スコア **: {score.total_score:.2f} ({score.grade})

| 指標 | スコア | 重み |
|------|--------|------|
| 決定完了率 | {score.decision_completion_rate:.0%} | 40% |
| リスク健全性 | {(1 - score.risk_rate):.0%} | 30% |
| ブロック解消率 | {(1 - score.block_rate):.0%} | 30% |

### 要注意項目
{_format_issues(state)}
"""


def _format_issues(state: "ProjectState") -> str:
    issues = []
    for task in state.blocked_tasks:
        issues.append(f"- 🔴 ブロック: {task['text']}")
    for risk in state.risks:
        if risk["severity"] == "high":
            issues.append(f"- ⚠️ 高リスク: {risk['text']}")
    for decision in state.decisions:
        if not decision["completed"]:
            issues.append(f"- ⏳ 未決定: {decision['text']}")
    return "\n".join(issues) if issues else "特になし"
```

---

## フェーズ4：Spec Aggregation — Spec 駆動ハンドオフ

Claude Code や他のLLMエージェントへタスクを渡す際、 ** コンテキストを構造化した Spec ファイル ** として出力します。

```python
# project_health/spec_generator.py
from datetime import datetime

def generate_spec(
    state: "ProjectState",
    score: "HealthScore",
    task_description: str,
    output_path: "Path",
) -> None:
    """エージェントへのハンドオフ用 Spec ファイルを生成する"""
    spec_content = f"""---
generated_at: {datetime.utcnow().isoformat()}
project: {state.name}
health_score: {score.total_score:.2f}
grade: {score.grade}
---

# タスク仕様書

## タスク概要
{task_description}

## 現在のプロジェクト状態

### 未完了の決定事項
{_format_open_decisions(state)}

### ブロックされたタスク
{_format_blocked(state)}

### 高リスク項目
{_format_high_risks(state)}

## 実装上の注意点

1. 上記の「未完了の決定事項」が実装に影響する場合は、先に決定を確認すること
2. ブロックされたタスクの依存関係を確認すること
3. 既存の実装ファイルを変更する場合は、この Spec を参照して影響範囲を確認すること

## 成功基準
- [ ] タスクが完了し、セッション記録に記載されている
- [ ] 影響するファイルが git commit されている
- [ ] MEMORY.md が最新状態に更新されている
"""

    output_path.write_text(spec_content, encoding="utf-8")
    print(f"Spec ファイル生成: {output_path}")


def _format_open_decisions(state: "ProjectState") -> str:
    open_decisions = [d for d in state.decisions if not d["completed"]]
    if not open_decisions:
        return "（なし）"
    return "\n".join(f"- {d['text']}" for d in open_decisions)


def _format_blocked(state: "ProjectState") -> str:
    if not state.blocked_tasks:
        return "（なし）"
    return "\n".join(f"- {t['text']}" for t in state.blocked_tasks)


def _format_high_risks(state: "ProjectState") -> str:
    high = [r for r in state.risks if r["severity"] == "high"]
    if not high:
        return "（なし）"
    return "\n".join(f"- {r['text']}" for r in high)
```

---

## フェーズ5：エージェントループの統合実行

```python
# project_health/runner.py
from pathlib import Path
from .init import load_project_state
from .reconcile import reconcile_files, check_git_status
from .scoring import calculate_health_score, format_health_report
from .spec_generator import generate_spec


def run_health_loop(
    project_root: Path,
    memory_path: Path,
    session_log: Path | None = None,
    task_description: str = "",
    generate_spec_file: bool = False,
) -> None:
    """プロジェクトヘルスループを実行する"""
    print(f"=== プロジェクトヘルスチェック: {project_root.name} ===\n")

    # フェーズ1: 状態初期化
    state = load_project_state(memory_path)
    print(f"読み込み完了: 決定事項={len(state.decisions)}, リスク={len(state.risks)}, ブロック={len(state.blocked_tasks)}")

    # フェーズ2: 照合（セッションログがある場合）
    if session_log and session_log.exists():
        reconcile_result = reconcile_files(session_log, project_root)
        if reconcile_result["missing"]:
            print(f"\n⚠️ セッション記録に記載のない実ファイル: {reconcile_result['missing']}")

    # git 状態確認
    git_status = check_git_status(project_root)
    if git_status["count"] > 0:
        print(f"\n⚠️ 未コミットファイル: {git_status['count']} 件")

    # フェーズ3: スコアリング
    score = calculate_health_score(state)
    report = format_health_report(state, score)
    print(report)

    # フェーズ4: Spec 生成（要求された場合）
    if generate_spec_file and task_description:
        spec_path = project_root / "specs" / f"handoff-{score.grade}.md"
        generate_spec(state, score, task_description, spec_path)

    print(f"\n=== 完了 ===")


# CLI として使う場合
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="プロジェクトヘルスチェック")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--memory", type=Path, help="MEMORY.md のパス")
    parser.add_argument("--session-log", type=Path)
    parser.add_argument("--task", type=str, default="")
    parser.add_argument("--spec", action="store_true")
    args = parser.parse_args()

    memory = args.memory or args.project_root / "MEMORY.md"
    run_health_loop(
        project_root=args.project_root,
        memory_path=memory,
        session_log=args.session_log,
        task_description=args.task,
        generate_spec_file=args.spec,
    )
```

---

## 実際の運用例

このスクリプトを毎日の作業開始時に実行しています：

```bash
python -m project_health.runner \
  ~/dev/projects/self/correlate-workspace \
  --session-log ~/dev/Obsidian/06_sessions/2026/04/2026-04-07-0900-xxx.md \
  --task "freee 入金通知の誤通知修正" \
  --spec
```

出力例：

```
=== プロジェクトヘルスチェック: correlate-workspace ===

読み込み完了: 決定事項=12, リスク=2, ブロック=1

## プロジェクトヘルスレポート

** プロジェクト **: correlate-workspace
** 総合スコア **: 0.82 (A-)

| 指標 | スコア | 重み |
|------|--------|------|
| 決定完了率 | 83% | 40% |
| リスク健全性 | 90% | 30% |
| ブロック解消率 | 75% | 30% |

### 要注意項目
- 🔴 ブロック: deals API のレート制限対策が未実装
- ⏳ 未決定: Realtime API の監視閾値
```

スコアが B 以下（0.7未満）になったときだけ詳細を確認する運用にしています。毎日全プロジェクトを目視確認するより、数値でフラグが立ったものだけ見ればよいので、判断コストが大幅に下がりました。

---

## 「LLM非依存」であることの意義

このスコアリングのポイントは **LLM を使わない ** ことです。

LLM に「このプロジェクトは健全ですか？」と聞いても、毎回異なる評価が返ってきます。コストもかかります。

決定論的なスコアリングにすることで：

- ** 再現性 ** — 同じ MEMORY.md からは必ず同じスコアが出る
- ** コスト 0** — スコアリング自体に API コストがかからない
- ** 速度 ** — 1秒以内に結果が出る
- ** デバッグ容易性 ** — スコアが下がった理由を式から逆算できる

LLM は「Spec ファイルを解釈して実装する」フェーズ（SWE Handoff）だけに使います。判断と実行を分離することで、エージェントループ全体の信頼性が上がります。

---

## まとめ

ADO の5フェーズエージェントループを個人開発に適用した実装を紹介しました：

1. **Init** — MEMORY.md からプロジェクト状態を構造化データとして読み込む
2. **Content Reconciliation** — セッション記録と実ファイルの乖離を検出する
3. **Health Monitoring** — 決定論的なスコアリングでプロジェクト健全性を定量化
4. **Spec Aggregation** — エージェントへのハンドオフ用 Spec ファイルを自動生成
5. **SWE Handoff** — Spec を持って Claude Code セッションへ渡す

企業向けの複雑なシステムも、 **Markdown + Python で本質は再現できます ** 。大切なのはアーキテクチャの思想を理解して、自分のスケールに落とし込むことです。

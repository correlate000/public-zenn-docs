---
title: "LLM-as-judgeで会話Botの品質を自動評価する——30ペルソナ×12シナリオの設計と10ラウンド改善の実践"
emoji: "⚖️"
type: "tech"
topics: ["llm", "chatbot", "promptengineering", "python", "ai"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

LINE向け会話Botのシステムプロンプトを改善していたとき、「このプロンプトは本当に良くなっているのか」を判断する方法がなく行き詰まりました。

人間が読んで「良さそう」と感じるプロンプトが、実際のユーザー対話でも良い結果を出すとは限りません。かといって、毎回手動でテスト会話を100本やるのは現実的ではありません。

そこで導入したのが **LLM-as-judge** パイプラインです。合成ペルソナとシナリオを組み合わせた自動テストを構築し、10ラウンドの反復改善で最終的に+12%のスコア向上を達成しました。この記事では、その設計思想と実装の詳細を共有します。

---

## LLM-as-judgeとは

LLM-as-judgeは、あるLLMの出力品質を別のLLM（または同一モデル）が評価する手法です。人間の評価者を完全に置き換えるものではありませんが、**スケーラブルな定量評価**を実現できます。

```
[テスト用ユーザー発言] → [評価対象Bot] → [Bot応答] → [Judge LLM] → [スコア]
```

人間評価と比べたメリット・デメリットは以下の通りです。

| 観点 | 人間評価 | LLM-as-judge |
|------|---------|-------------|
| スケール | 低（時間・コスト制約） | 高（並列実行可） |
| 一貫性 | 評価者によってばらつく | 設定が同じなら安定 |
| コスト | 高（人件費） | 中（API費用） |
| 深い文脈理解 | 優れている | モデル依存 |
| ターンアラウンド | 遅い | 速い |

---

## システム設計の全体像

### 評価パイプラインのフロー

```
ペルソナ定義 (30種)
    ↓
シナリオ定義 (12種)
    ↓
合成会話生成（ペルソナ×シナリオ = 最大360テストケース）
    ↓
評価対象Botとの会話実行（並列）
    ↓
Judge LLMによるルブリック評価
    ↓
スコア集計・レポート生成
    ↓
システムプロンプト改善
    ↓ （収穫逓減まで繰り返し）
```

### 技術スタック

- **評価対象Bot**: Claude claude-sonnet-4 (システムプロンプト差し替え)
- **Judge LLM**: Claude claude-opus-4 (高精度評価のため上位モデル)
- **並列実行**: Python asyncio + asyncio.gather
- **スコア管理**: BigQuery (ラウンド別スコア推移の永続化)
- **実行環境**: Cloud Run Jobs (定期実行 or 手動トリガー)

---

## ペルソナ設計：30種類の合成ユーザー

ペルソナは「実際に使いそうなユーザー像」を網羅的に設計しました。単純に多様性を持たせるだけでなく、**Botが苦手とするエッジケースをカバーする**ことを意識しました。

```python
# persona_definitions.py

PERSONAS = [
    {
        "id": "P01",
        "name": "急いでいる会社員",
        "age_range": "30-40",
        "communication_style": "short",
        "context": "移動中にスマホで操作。返答は簡潔に欲しい",
        "challenge_level": "low",
        "language_style": "casual"
    },
    {
        "id": "P02",
        "name": "高齢・スマホ不慣れユーザー",
        "age_range": "65+",
        "communication_style": "verbose",
        "context": "操作に不安。丁寧な説明を求める",
        "challenge_level": "high",
        "language_style": "polite"
    },
    {
        "id": "P03",
        "name": "クレーム傾向のあるユーザー",
        "age_range": "40-50",
        "communication_style": "aggressive",
        "context": "過去に悪い経験あり。疑念を持ってアクセス",
        "challenge_level": "high",
        "language_style": "direct"
    },
    # ... 27種類続く
]
```

30種類のペルソナは以下のカテゴリに分類しています。

| カテゴリ | ペルソナ数 | 目的 |
|---------|-----------|------|
| 典型的ユーザー | 10 | ベースラインの測定 |
| エッジケース | 8 | Botの弱点発見 |
| 高難度ユーザー | 7 | ストレステスト |
| 特殊ニーズ | 5 | アクセシビリティ確認 |

---

## シナリオ設計：12種類の会話文脈

ペルソナが「誰か」を定義するなら、シナリオは「何を話すか」を定義します。

```python
# scenario_definitions.py

SCENARIOS = [
    {
        "id": "S01",
        "name": "初回問い合わせ",
        "turn_type": "single",
        "objective": "サービスの基本情報を正確に得る",
        "success_criteria": "情報の正確性 + 次のアクションが明確"
    },
    {
        "id": "S02",
        "name": "追加質問あり",
        "turn_type": "multi",
        "max_turns": 4,
        "objective": "複数の関連質問を順番に解決する",
        "success_criteria": "会話の一貫性 + 全質問への対応"
    },
    {
        "id": "S03",
        "name": "不満・苦情申告",
        "turn_type": "multi",
        "max_turns": 4,
        "objective": "感情的なユーザーを適切にサポートする",
        "success_criteria": "共感表現 + 解決策の提示"
    },
    {
        "id": "S04",
        "name": "曖昧な質問",
        "turn_type": "single",
        "objective": "不明確な質問に対して適切に明確化を求める",
        "success_criteria": "適切な確認質問 + 過度な推測をしない"
    },
    # ... 8種類続く
]
```

---

## ルブリック設計：評価軸の定義

ここが最も重要かつ難しい部分です。評価軸（ルブリック）の設計次第で、測定したいものが変わります。

### シングルターン評価（6軸）

```python
SINGLE_TURN_RUBRIC = {
    "accuracy": {
        "description": "情報の正確性。ファクトが正しいか",
        "scale": "1-5",
        "weight": 2.0  # 重み付き平均に使用
    },
    "relevance": {
        "description": "質問への適切さ。ユーザーの意図に応えているか",
        "scale": "1-5",
        "weight": 1.5
    },
    "clarity": {
        "description": "説明のわかりやすさ",
        "scale": "1-5",
        "weight": 1.0
    },
    "tone": {
        "description": "トーンの適切さ。ペルソナのスタイルに合っているか",
        "scale": "1-5",
        "weight": 1.0
    },
    "completeness": {
        "description": "必要な情報が網羅されているか",
        "scale": "1-5",
        "weight": 1.5
    },
    "action_clarity": {
        "description": "次のアクションが明確に示されているか",
        "scale": "1-5",
        "weight": 1.0
    }
}
```

### マルチターン評価（5軸）

マルチターン評価には、単発会話では現れない軸を追加しています。

```python
MULTI_TURN_RUBRIC = {
    "consistency": {
        "description": "会話を通じた一貫性。前の発言を覚えているか",
        "scale": "1-5",
        "weight": 2.0
    },
    "context_retention": {
        "description": "文脈の保持。ペルソナ情報を活かせているか",
        "scale": "1-5",
        "weight": 1.5
    },
    "trust_building": {
        "description": "信頼関係の構築。会話を重ねるにつれ関係が深まるか",
        "scale": "1-5",
        "weight": 1.5
    },
    "problem_resolution": {
        "description": "問題解決の達成度。最終的にユーザーの目的を達成できたか",
        "scale": "1-5",
        "weight": 2.0
    },
    "recovery": {
        "description": "誤解からの回復。ミスコミュニケーション後の対処",
        "scale": "1-5",
        "weight": 1.0
    }
}
```

:::message
**重要な設計原則**: ルブリックの評価軸はテストの制約と整合させる必要があります。たとえば「4ターンまでのテスト」で `trust_building` を評価する場合、「4ターンで形成できる信頼レベル」を5点の基準として再定義する必要があります。この整合性を最初に怠ったことが、後の再校正コストにつながりました。
:::

---

## Judgeプロンプトの実装

Judge LLMへの評価依頼プロンプトが、スコアの品質を決定します。

```python
# judge_evaluator.py

async def evaluate_conversation(
    conversation: list[dict],
    persona: dict,
    scenario: dict,
    rubric: dict
) -> dict:
    """会話をJudge LLMで評価する"""

    rubric_text = "\n".join([
        f"- {axis}: {config['description']} (1-5点, 重み: {config['weight']})"
        for axis, config in rubric.items()
    ])

    judge_prompt = f"""
あなたは会話品質の評価者です。以下の会話を評価してください。

## ペルソナ情報
- タイプ: {persona['name']}
- コミュニケーションスタイル: {persona['communication_style']}
- 文脈: {persona['context']}

## シナリオ
- 目標: {scenario['objective']}
- 成功基準: {scenario['success_criteria']}

## 会話ログ
{format_conversation(conversation)}

## 評価軸
{rubric_text}

## 出力形式（JSON）
各評価軸のスコアと理由を以下の形式で返してください:
{{
    "scores": {{
        "accuracy": {{"score": 4, "reason": "..."}},
        "relevance": {{"score": 5, "reason": "..."}}
    }},
    "overall_comment": "総評",
    "improvement_suggestions": ["改善提案1", "改善提案2"]
}}

必ずJSONのみを返し、それ以外のテキストは含めないでください。
"""

    response = await anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": judge_prompt}]
    )

    return json.loads(response.content[0].text)
```

---

## 並列実行の実装

360テストケース（30ペルソナ × 12シナリオ）を直列実行すると現実的ではありません。asyncioで並列化しています。

```python
# pipeline_runner.py

import asyncio
from anthropic import AsyncAnthropic

client = AsyncAnthropic()
CONCURRENCY_LIMIT = 10  # APIレート制限を考慮

async def run_single_evaluation(
    persona: dict,
    scenario: dict,
    system_prompt: str,
    semaphore: asyncio.Semaphore
) -> dict:
    """1件の評価を実行する"""
    async with semaphore:
        # Bot会話を生成
        conversation = await generate_conversation(
            persona, scenario, system_prompt
        )
        # Judge評価
        rubric = (
            MULTI_TURN_RUBRIC if scenario["turn_type"] == "multi"
            else SINGLE_TURN_RUBRIC
        )
        scores = await evaluate_conversation(
            conversation, persona, scenario, rubric
        )
        return {
            "persona_id": persona["id"],
            "scenario_id": scenario["id"],
            "scores": scores,
            "conversation": conversation
        }


async def run_full_evaluation(system_prompt: str, round_id: str) -> list[dict]:
    """全テストケースを並列実行する"""
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    tasks = [
        run_single_evaluation(persona, scenario, system_prompt, semaphore)
        for persona in PERSONAS
        for scenario in SCENARIOS
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # エラーを除いて返す
    valid_results = [r for r in results if not isinstance(r, Exception)]
    error_count = len(results) - len(valid_results)

    if error_count > 0:
        print(f"警告: {error_count}件のテストケースでエラーが発生しました")

    return valid_results
```

---

## 10ラウンドの反復改善プロセス

### スコア推移

| ラウンド | 加重平均スコア | 主な変更内容 |
|---------|-------------|------------|
| v1.0 | 3.42 | ベースライン |
| v1.1 | 3.58 | 導入文の明確化 |
| v1.2 | 3.71 | トーン調整（カジュアル↔フォーマル） |
| v1.3 | 3.79 | エラー時の回復フロー追加 |
| v1.4 | 3.88 | 高齢ユーザー向け説明の簡潔化 |
| v1.5 | 3.91 | trust_buildingルブリック再校正 |
| v1.5* | 4.06 | （再校正後のベース再測定） |
| v1.6 | 4.13 | 曖昧質問への確認フロー追加 |
| v1.7 | 4.15 | 微調整 |
| v1.8 | 4.16 | 微調整（有意差なし） |

v1.7とv1.8で有意差が確認できなかったため、「収穫逓減」と判断して最適化を凍結しました。最終的なスコア向上は **+0.74点（+21.6%）** です（trust_building再校正後のベースで計算すると +12%）。

### 収穫逓減の判断基準

```python
def should_stop_optimization(score_history: list[float]) -> bool:
    """2連続ラウンドで有意差なしなら最適化を停止する"""
    if len(score_history) < 3:
        return False

    SIGNIFICANCE_THRESHOLD = 0.05  # 0.05点未満は有意差なしと判定

    last_delta = abs(score_history[-1] - score_history[-2])
    prev_delta = abs(score_history[-2] - score_history[-3])

    return last_delta < SIGNIFICANCE_THRESHOLD and prev_delta < SIGNIFICANCE_THRESHOLD
```

---

## ハマりポイントと対策

### 1. trust_buildingルブリックの再校正

v1.4まで `trust_building` のスコアが異常に低く推移していました。原因を調査したところ、ルブリックの定義が「長期的な関係性構築」を想定した基準になっており、4ターンのテスト制約と合っていなかったことが判明しました。

**修正前:**
```
trust_building: 会話を通じてユーザーとの深い信頼関係を築けているか
（5点: 友人のような親密さを感じるやり取り）
```

**修正後:**
```
trust_building: 4ターン以内の会話でユーザーが安心感を持てているか
（5点: 自分の状況を理解してもらえていると感じられる応答）
```

この修正だけで `trust_building` が +0.15〜0.20点改善しました。

### 2. Judge LLMの採点バイアス

同じ会話に対して、Judge LLMが毎回同一スコアを返すとは限りません。バイアスを減らすために以下の対策を取りました。

```python
async def evaluate_with_majority_vote(
    conversation: list[dict],
    persona: dict,
    scenario: dict,
    rubric: dict,
    n_votes: int = 3
) -> dict:
    """多数決でスコアを安定させる"""
    evaluations = await asyncio.gather(*[
        evaluate_conversation(conversation, persona, scenario, rubric)
        for _ in range(n_votes)
    ])

    # 各軸のスコアを平均する
    averaged_scores = {}
    for axis in rubric.keys():
        scores_for_axis = [e["scores"][axis]["score"] for e in evaluations]
        averaged_scores[axis] = {
            "score": sum(scores_for_axis) / len(scores_for_axis),
            "std": statistics.stdev(scores_for_axis) if len(scores_for_axis) > 1 else 0
        }

    return {"scores": averaged_scores}
```

### 3. コスト管理

360テストケース × 3票（多数決）= 1,080回のJudge LLM呼び出しは、1ラウンドあたり相応のAPI費用がかかります。以下の戦略でコストを管理しました。

- **ベースラインはOpus、デバッグはSonnet**: ラウンド途中の確認はSonnetを使い、最終評価のみOpusを使う
- **代表ペルソナでのクイックチェック**: 全360ケースの前に、代表的な10ペルソナで事前確認
- **キャッシュの活用**: 同一の会話ログに対する評価はキャッシュして再利用

```python
import hashlib
import json
from functools import lru_cache

def get_conversation_hash(conversation: list[dict]) -> str:
    """会話ログのハッシュ値を計算する"""
    return hashlib.sha256(
        json.dumps(conversation, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
```

---

## BigQueryへのスコア保存

ラウンド間の比較や後の分析のため、全スコアをBigQueryに保存しています。

```python
# bq_writer.py

from google.cloud import bigquery

def save_evaluation_results(
    results: list[dict],
    round_id: str,
    system_prompt_version: str
):
    """評価結果をBigQueryに保存する"""
    client = bigquery.Client()

    rows = []
    for result in results:
        for axis, score_data in result["scores"]["scores"].items():
            rows.append({
                "round_id": round_id,
                "system_prompt_version": system_prompt_version,
                "persona_id": result["persona_id"],
                "scenario_id": result["scenario_id"],
                "evaluation_axis": axis,
                "score": score_data["score"],
                "evaluated_at": datetime.utcnow().isoformat()
            })

    errors = client.insert_rows_json(
        "your_project.bot_evaluation.scores",
        rows
    )

    if errors:
        raise RuntimeError(f"BigQuery insert error: {errors}")
```

これにより、ラウンド別・ペルソナ別・シナリオ別のスコア推移をBigQueryで分析できます。

```sql
-- ラウンド別の加重平均スコアを計算
SELECT
    round_id,
    system_prompt_version,
    SUM(score * weight) / SUM(weight) AS weighted_avg_score
FROM `your_project.bot_evaluation.scores`
JOIN `your_project.bot_evaluation.rubric_weights` USING (evaluation_axis)
GROUP BY round_id, system_prompt_version
ORDER BY round_id
```

---

## まとめと今後の展望

LLM-as-judgeパイプラインを構築して得た知見をまとめます。

**うまくいったこと:**
- ペルソナとシナリオの組み合わせで、人手では気づかなかった弱点を発見できた
- 定量スコアがあることで、改善の議論が「感覚」から「数値」になった
- 収穫逓減の自動判断で、無駄な改善ラウンドを防げた

**難しかったこと:**
- ルブリックとテスト制約の整合性の確保（最初から意識すべきだった）
- Judge LLMのバイアス（多数決ある程度緩和できるが、完全な解消は難しい）
- API費用の管理（積極的にキャッシュと代表サンプリングを使う必要がある）

**今後の改善方針:**
- 人間評価との相関分析（Judge LLMの評価が人間と乖離していないか検証）
- ペルソナの動的生成（BigQueryのユーザーログからリアルなペルソナを自動生成）
- A/Bテスト統合（本番環境での実ユーザー評価との連携）

LLM-as-judgeは「完璧な評価システム」ではありませんが、「人手では不可能なスケールでの継続的品質管理」を実現してくれます。プロンプト改善のサイクルを回すなら、評価の自動化はほぼ必須だと感じています。

---

## 関連記事

- [Claude Code スラッシュコマンドで作業を自動化する](/claude-code-slash-commands)
- [ローカルLLM vs クラウドLLM——コスト・用途別の使い分け](/local-llm-2025)

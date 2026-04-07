---
title: "Qwen3-Next 80B × Claude Opus 4.6 ハイブリッド運用で月$480削減した話"
emoji: "🔀"
type: "tech"
topics: ["llm", "localllm", "claude", "mlx", "costreduction"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「ローカルLLMに切り替えればAPIコストをゼロにできる」という話を聞いたとき、あなたはどう感じましたか？

正直に言うと、筆者もその甘い言葉に乗りました。Claude Opus 4.6の月額利用料が$680を超え始めたころ、「80%をローカルに置き換えれば大幅削減できるはず」と考え、Qwen3-Next 80BをMac mini M4 Pro上でMLX動作させるハイブリッド構成に踏み切ったのです。

結果から言えば、月$480の削減には成功しました。しかし道中には想定外の失敗が3つありました。本記事では、その失敗を含めた実運用の全体像と、実際に動かしているルーティングコードを共有する。

### 対象読者

- Claude APIのコストが月$300以上になっている個人事業主・スタートアップのエンジニア
- Apple Siliconマシン（Mac mini/MacBook Pro）でローカルLLMを試したい方
- 「全部ローカルに移行」ではなく「賢い使い分け」に興味がある方

---

## 失敗① セットアップは「簡単」ではない

最初に試したのはOllamaです。公式ドキュメントは確かにシンプルで、`ollama run qwen3:72b` の一行でモデルが動き始めます。ただし、これはスタートラインに過ぎない。

### 最初の3日間でぶつかった壁

**MacBook Pro M2 16GBでの失敗 **

Qwen3-Next 80BのQ4量子化モデルは約45GBあります。16GBのユニファイドメモリでは完全にメモリに乗らず、スワップが発生して推論速度が1〜3 tokens/secまで落ちました。実用に耐えないレベルだ。

| 環境 | 推論速度 | 状態 |
|------|---------|------|
| MacBook Pro M2 16GB | 1〜3 tokens/sec | スワップ多発・実用不可 |
| Mac mini M4 Pro 64GB | 33 tokens/sec | 安定稼働中 |

この失敗でMac mini M4 Pro 64GBを追加購入することになりました。メモリ64GBあれば45GBモデルを余裕を持って展開でき、推論中のメモリ使用量は実測で約2.32GBのオーバーヘッドに収まっています。

**MLX vs Ollama**

Ollamaはシンプルですが、Apple Silicon向けの最適化はMLXフレームワークほど深くありません。`mlx-lm` を使うと同じモデルで推論速度が約1.4倍向上しました。ただしMLXの場合、モデルをHugging Face形式からMLX形式に変換する手順が必要になる。

```bash
# MLX形式への変換
pip install mlx-lm
python -m mlx_lm.convert \
  --hf-path Qwen/Qwen3-Next-72B \
  --mlx-path ~/models/qwen3-next-72b-mlx \
  -q  # 4bit量子化
```

変換には約30分かかります。ディスク容量も変換前後で合計60GB以上必要なので注意してください。

---

## 失敗② 管理・保守コストを過小評価していた

「ローカルLLMはAPIと違って固定費がゼロ」という認識は半分正解で半分誤りです。直接的な課金はありませんが、管理コストが発生します。

### 実際にかかった月次作業（時間単価換算）

| 作業 | 頻度 | 所要時間 |
|------|------|---------|
| モデルのアップデート確認・差し替え | 月1回 | 2〜3時間 |
| 推論サーバーの死活監視設定調整 | 月2回程度 | 1時間 |
| パフォーマンス低下時の診断 | 不定期 | 1〜4時間 |
| セキュリティパッチ（Pythonライブラリ等） | 月1回 | 30分 |

月あたり5〜8時間の保守コストが発生しています。時給換算すると無視できない数字です。APIを使っていれば発生しないコストであることを念頭に置く必要がある。

### launchdでの自動起動設定

Mac miniの場合、再起動時に推論サーバーが自動起動しないと業務が止まります。launchdを使って常駐設定しています。

```xml
<!-- ~/Library/LaunchAgents/jp.correlate.mlx-server.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>jp.correlate.mlx-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/naoya/.pyenv/shims/python</string>
    <string>-m</string>
    <string>mlx_lm.server</string>
    <string>--model</string>
    <string>/Users/naoya/models/qwen3-next-72b-mlx</string>
    <string>--port</string>
    <string>8080</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/naoya/dev/logs/mlx-server.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/naoya/dev/logs/mlx-server-error.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/jp.correlate.mlx-server.plist
```

---

## 失敗③ 日本語品質のトレードオフを甘く見ていた

Qwen3-Nextは中国のAlibabaが開発したモデルで、中国語・英語の性能が特に高い設計です。日本語の品質は「実用水準」ではありますが、Claude Opus 4.6と比べると差があります。

### 日本語での主な差異

筆者の実運用で確認した差異を整理します。

**Qwen3-Next 80Bが得意な領域（Claude比で遜色ない） **

- コードの生成・デバッグ（言語非依存のため）
- 英語文書の日本語要約
- 定型フォーマットへの情報整理
- 数値・データの集計・分析

**Claude Opus 4.6が必要な領域 **

- 日本語の文体・ニュアンスが重要なコンテンツ執筆
- 複雑な法的・倫理的判断を要するタスク
- 曖昧な要件からの設計判断
- 多段階の推論が必要な問題解決

この差を無視して全タスクをQwen3-Nextに流すと、アウトプットの品質が下がります。「コスト削減のためにローカルに全量移行」という判断は、品質リスクを伴います。

---

## 成功：ハイブリッド方式への転換

3つの失敗を経て、現在は以下の棲み分けに落ち着いています。

| タスク区分 | 割合 | 使用モデル | 理由 |
|-----------|------|-----------|------|
| コード生成・デバッグ | 40% | Qwen3-Next 80B | 品質差小さく高速 |
| データ処理・分析 | 20% | Qwen3-Next 80B | 数値処理は同等 |
| 文書要約・整理 | 20% | Qwen3-Next 80B | 定型的で品質十分 |
| 設計判断・執筆 | 15% | Claude Opus 4.6 | 品質が重要 |
| セキュリティ・法的判断 | 5% | Claude Opus 4.6 | リスク許容不可 |

結果として ** 約80%のタスクをQwen3-Nextで処理 ** できるようになり、月$680→$200へのコスト削減を達成しました。

---

## 実装：タスクルーティングの仕組み

ポイントは「タスクごとにモデルを手動で切り替える」ではなく、 ** ルーターで自動振り分ける ** ことです。

### task_router.py の全体構成

```python
"""
task_router.py — LLMタスクルーティングモジュール
ローカルLLM(Qwen3-Next)とClaude APIを自動振り分けする
"""

import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class ModelChoice(Enum):
    LOCAL = "local"    # Qwen3-Next 80B (MLX)
    CLAUDE = "claude"  # Claude Opus 4.6


@dataclass
class RoutingResult:
    model: ModelChoice
    reason: str
    confidence: float  # 0.0-1.0


# Claude必須タスクのキーワードパターン
CLAUDE_REQUIRED_PATTERNS = [
    # 設計・判断系
    r"設計.*判断|アーキテクチャ.*提案|技術選定",
    r"セキュリティ.*レビュー|脆弱性.*分析",
    r"法的|規約|契約|コンプライアンス",
    # 文体・品質系
    r"記事.*執筆|ブログ.*作成|コピー.*ライティング",
    r"提案書|企画書|報告書.*作成",
    r"日本語.*添削|文章.*改善",
    # 複雑な推論系
    r"なぜ.*か.*説明|根本原因.*分析",
    r"複数.*案.*比較|トレードオフ.*分析",
]

# ローカルで十分なタスクのパターン
LOCAL_SUFFICIENT_PATTERNS = [
    r"コード.*生成|関数.*実装|バグ.*修正",
    r"テスト.*コード|ユニットテスト",
    r"要約|サマリー|まとめ",
    r"データ.*集計|CSV.*処理|JSON.*パース",
    r"英語.*翻訳|翻訳.*日本語",
    r"コメント.*追加|ドキュメント.*コメント",
    r"リファクタリング|コード.*整形",
]


def route_task(
    prompt: str,
    max_tokens: int = 2000,
    force_model: Optional[ModelChoice] = None,
) -> RoutingResult:
    """
    タスクの内容を分析してモデルを選択する

    Args:
        prompt: ユーザーからのプロンプト
        max_tokens: 期待するレスポンスの最大トークン数
        force_model: 強制モデル指定（テスト・デバッグ用）

    Returns:
        RoutingResult: 選択されたモデルと理由
    """
    if force_model:
        return RoutingResult(
            model=force_model,
            reason="force_model指定",
            confidence=1.0,
        )

    # Claude必須パターンの確認
    for pattern in CLAUDE_REQUIRED_PATTERNS:
        if re.search(pattern, prompt):
            return RoutingResult(
                model=ModelChoice.CLAUDE,
                reason=f"Claude必須パターン検出: {pattern}",
                confidence=0.9,
            )

    # 長文・複雑な推論が必要な場合はClaude
    if max_tokens > 4000:
        return RoutingResult(
            model=ModelChoice.CLAUDE,
            reason="長文生成タスク（max_tokens > 4000）",
            confidence=0.8,
        )

    # ローカルで十分なパターンの確認
    for pattern in LOCAL_SUFFICIENT_PATTERNS:
        if re.search(pattern, prompt):
            return RoutingResult(
                model=ModelChoice.LOCAL,
                reason=f"ローカル十分パターン: {pattern}",
                confidence=0.85,
            )

    # デフォルト：ローカルを試みる（不確かなケース）
    return RoutingResult(
        model=ModelChoice.LOCAL,
        reason="デフォルトルーティング（ローカル優先）",
        confidence=0.6,
    )
```

### APIラッパーとの統合

```python
"""
llm_client.py — モデル統合クライアント
ルーティング結果に基づいてローカルまたはClaude APIを呼び出す
"""

import os
import httpx
import anthropic
from task_router import route_task, ModelChoice

LOCAL_API_URL = "http://localhost:8080/v1/chat/completions"
LOCAL_MODEL_NAME = "qwen3-next-72b"


class HybridLLMClient:
    def __init__(self):
        self.claude = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"]
        )
        self.local_client = httpx.Client(timeout=120.0)

    def complete(
        self,
        prompt: str,
        max_tokens: int = 2000,
        system: str = "",
    ) -> dict:
        routing = route_task(prompt, max_tokens)

        if routing.model == ModelChoice.LOCAL:
            return self._call_local(prompt, max_tokens, system, routing)
        else:
            return self._call_claude(prompt, max_tokens, system, routing)

    def _call_local(self, prompt, max_tokens, system, routing) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self.local_client.post(
            LOCAL_API_URL,
            json={
                "model": LOCAL_MODEL_NAME,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
        )
        response.raise_for_status()
        data = response.json()

        return {
            "content": data["choices"][0]["message"]["content"],
            "model": "local/qwen3-next",
            "routing_reason": routing.reason,
            "cost_usd": 0.0,  # ローカルは課金なし
        }

    def _call_claude(self, prompt, max_tokens, system, routing) -> dict:
        kwargs = {
            "model": "claude-opus-4-6",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        message = self.claude.messages.create(**kwargs)

        # コスト計算（概算）
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        cost = (input_tokens * 15 + output_tokens * 75) / 1_000_000

        return {
            "content": message.content[0].text,
            "model": "claude-opus-4-6",
            "routing_reason": routing.reason,
            "cost_usd": cost,
        }
```

### 使い方

```python
client = HybridLLMClient()

# コード生成 → 自動でローカルにルーティング
result = client.complete(
    prompt="PythonでCSVファイルを読み込んで集計するコードを生成してください"
)
print(f"使用モデル: {result['model']}")  # → local/qwen3-next
print(f"コスト: ${result['cost_usd']:.4f}")  # → $0.0000

# 提案書作成 → 自動でClaudeにルーティング
result = client.complete(
    prompt="クライアントへのシステム移行提案書を作成してください",
    max_tokens=5000,
)
print(f"使用モデル: {result['model']}")  # → claude-opus-4-6
print(f"コスト: ${result['cost_usd']:.4f}")  # → $0.0120
```

---

## ROIシミュレーション

導入を検討する際のROI計算例を示します。

### 前提条件

- Claude APIの月額コスト（導入前）: $680
- Mac mini M4 Pro 64GB購入費: 約24万円（$1,600）
- 電力コスト: 月額約$5（30W × 720時間）
- 管理工数: 月6時間（時給$50換算 = $300）

### 月次コスト比較

| 項目 | 導入前 | 導入後 |
|------|--------|--------|
| Claude API | $680 | $200 |
| ローカルサーバー（電力） | $0 | $5 |
| 管理工数（機会コスト） | $0 | $300 |
| 合計 | $680 | $505 |

見かけの削減額は$480ですが、管理工数を含めると実質の削減額は$175/月です。

### 回収期間

初期投資$1,600 ÷ 月$175削減 ≈ ** 約9ヶ月 **

管理工数を時給$50で評価すると9ヶ月の回収期間となります。管理をどこまで自動化できるか、あるいは自分の時給をどう評価するかで数字は変わります。

### ローカルLLM導入が合うケース・合わないケース

** 合うケース **

- コード生成・データ処理が業務の大半を占める
- 常時稼働のマシンがすでにある（追加コストが電力のみ）
- 情報セキュリティ上、APIに送れないデータを扱う
- 月のAPIコストが$500以上で、エンジニアが保守できる

** 合わないケース **

- 日本語の文体品質が最重要なコンテンツ業務が中心
- 保守作業に割く時間がない
- 月のAPIコストが$200未満（ROIが出にくい）
- Mac mini等の専用ハードウェアへの初期投資が困難

---

## 運用6週間で学んだ10のこと

1. **64GB以上のメモリが必須条件 **: 80Bモデルを安定稼働させるには64GB必要です。32GBでも動作はしますが、速度と安定性が犠牲になります

2. **MLXはOllamaより1.4倍速い **: Apple Siliconを使うならMLXフレームワークを選んでください。変換の手間はありますが効果は明確です

3. ** ルーティングのデフォルトはローカル優先に **: 迷ったらローカルに流し、品質が不十分なら手動でClaudeへ切り替える運用の方がコスト最適化しやすいです

4. ** モデルの更新サイクルは速い **: Qwen3-Nextは2ヶ月で複数バージョンが出ました。変換済みモデルのバージョン管理が必要です

5. ** 推論ログは必ず記録する **: どのタスクがどのモデルに流れたか、コストはいくらかを記録しておくとルーター改善に役立ちます

6. ** 温度設定はコード0.1、文書0.7**: タスク種別に応じてtemperatureを変えると品質が安定します

7. ** コンテキスト長の差に注意 **: Qwen3-Next 80Bは最大32Kトークンです。長い文書を扱う場合はClaude（200K）の方が適切なケースがあります

8. ** 停電・スリープ後のサーバー復旧確認を自動化 **: launchdのKeepAlive設定だけでなく、ヘルスチェックAPIを定期的にpingする監視スクリプトも用意しています

9. ** ルーティング精度は定期的に見直す **: パターンマッチングは万能ではありません。月次でルーティング結果を見直してパターンを調整しています

10. ** 「全部ローカル」は理想論 **: コスト最適化の目的はROI最大化であり、コストゼロではありません。品質とコストのバランスを取る判断が最も重要です

---

## まとめ

Qwen3-Next 80B × Claude Opus 4.6のハイブリッド運用は、正しく設計すれば効果的なコスト削減手段です。ただし「ローカルLLMに切り替えれば全部解決」という期待は裏切られます。

現実的な数字をまとめると：

- ** 見かけのAPI削減額 **: 月$480（$680 → $200）
- ** 管理コスト **: 月$300（6時間 × 時給$50）
- ** 実質の純削減額 **: 月$175前後
- ** 初期投資の回収期間 **: 約9ヶ月

それでも、コード・データ処理系の業務が多く、Mac miniの保守を許容できるなら、1年以上の運用で明確なプラスになります。また、APIに送れない機密データをローカルで処理できるというセキュリティ上のメリットは、コスト以外の観点でも価値があります。

ローカルLLMは「銀の弾」ではありませんが、「適切な道具」として機能します。業務プロファイルを分析してから、ハイブリッド比率を設計してみてください。

---

## 参考リソース

- [Alibaba Qwen公式（Hugging Face）](https://huggingface.co/Qwen)
- [MLX — Apple Siliconフレームワーク](https://ml-explore.github.io/mlx/)
- [LM Studio](https://lmstudio.ai/)
- [Ollama](https://ollama.ai/)

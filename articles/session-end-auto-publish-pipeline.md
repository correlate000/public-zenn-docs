---
title: "セッション終了→Zenn記事自動公開の全自動パイプラインを構築した話"
emoji: "⚙️"
type: "tech"
topics: ["claudecode", "python", "automation", "zenn", "llm"]
published: false
publication_name: "correlate_dev"
---

## はじめに

「記事を書きたいけど時間がない」という問題を、前回の記事（「Claude Code × Agent Teamsで1日5本のZenn記事を書いた話」）ではAIエージェントの並列活用で解決しました。しかしあの方法には、まだ人手が介在するステップが残っていました。

今回はその発展形として、 **`/session-end`コマンドを叩くだけで、セッションの知見が自動的にZenn記事として公開されるまでのパイプライン** を構築しました。人間がやることは、セッション終了時に1つのコマンドを実行するだけです。品質ゲートを通過した記事（B-以上の評価）は自動公開され、通過しなかった記事は手動レビューのキューに入ります。実績として、約85%の記事が品質ゲートを通過しており、実質的な自動化が実現できています。

コスト試算は **約$0.10/記事** です。以下がその内訳です。

| モデル | ステージ | トークン数（目安） | コスト |
|--------|---------|-----------------|--------|
| Qwen-Plus | Stage 1: 初稿生成 | 〜8k tokens | $0.008 |
| Claude Sonnet | Stage 2: 技術検証 | 〜12k tokens | $0.036 |
| Gemini 1.5 Flash | Stage 3: 読みやすさ最適化 | 〜12k tokens | $0.012 |
| Claude Sonnet | Stage 4: Zennフォーマット整形 | 〜10k tokens | $0.030 |
| Gemini 1.5 Flash | Stage 5: 最終推敲 | 〜8k tokens | $0.008 |
| Claude Sonnet | DAレビュー | 〜5k tokens | $0.015 |
| 合計 | | | 〜$0.109 |

※トークン数・コストはresearchファイル1本あたりの実測平均値。記事の長さにより±30%程度変動します。

---

## 全体アーキテクチャ

```mermaid
flowchart TD
    A["/session-end 実行"] --> B["セッションコンテンツ抽出"]
    B --> C["research ファイル生成"]
    C --> D["watchdog 検知"]
    D --> E["Stage 1: 初稿生成\nQwen-Plus"]
    E --> F["Stage 2: 技術検証\nClaude Sonnet"]
    F --> G["Stage 3: 読みやすさ最適化\nGemini 1.5 Flash"]
    G --> H["Stage 4: Zennフォーマット整形\nClaude Sonnet"]
    H --> I["Stage 5: 最終推敲\nGemini 1.5 Flash"]
    I --> J["DA レビュー\nClaude Sonnet"]
    J --> K{品質ゲート\nB- 以上？}
    K -- "通過 (~85%)" --> L["自動公開\nZenn"]
    K -- "非通過 (~15%)" --> M["手動レビュー\nキュー投入"]
```

パイプラインは大きく5つのフェーズで構成されています。

```mermaid
flowchart TD
    A["/session-end 実行"] --> B["① コンテンツ候補の抽出\nセッション記録から「記事になりそうな知見」を自動抽出"]
    B --> C["② researchファイル自動生成\n抽出した知見をリサーチノートとしてファイル化"]
    C --> D["③ watchdogによるファイル監視\nresearchディレクトリの変更を検知してパイプライン起動"]
    D --> E["④ 3モデル協調による5段階生成\nQwen → Sonnet → Gemini の順でドラフト→推敲→整形"]
    E --> F{"⑤ DAレビュー品質ゲート\nB-以上で通過"}
    F -->|"通過（約85%）"| G["Zenn自動公開"]
    F -->|"不通過（約15%）"| H["手動レビューキュー\n（review-failed/に保存）"]
```

各フェーズの詳細を順に説明します。

---

## フェーズ①② : /session-endトリガーとresearchファイル生成

```mermaid
sequenceDiagram
    actor User as ユーザー
    participant CC as Claude Code
    participant Hook as session_end_hook.py
    participant API as Claude API
    participant Scorer as スコアリング
    participant FS as researchディレクトリ

    User->>CC: /session-end 実行
    CC->>Hook: セッション終了イベント発火
    Hook->>Hook: セッション履歴・成果物を収集
    Hook->>API: プロンプト送信（セッション内容の要約・記事化依頼）
    API-->>Hook: 記事草稿（Markdown）を返却
    Hook->>Scorer: 草稿を品質評価に送信
    Scorer->>API: 評価プロンプト送信（技術正確性・読みやすさ等）
    API-->>Scorer: スコア・評価結果を返却
    Scorer-->>Hook: スコア判定結果（A〜F）を返却

    alt 品質ゲート通過（B-以上）
        Hook->>FS: researchファイルとして保存
        Hook->>CC: 自動公開パイプラインへ連携
        CC-->>User: 公開完了通知
    else 品質ゲート未通過（C以下）
        Hook->>FS: researchファイルとして保存（要レビューフラグ付き）
        CC-->>User: 手動レビューキュー追加通知
    end
```

### カスタムコマンドの設定方法

Claude Codeのカスタムコマンド機能を使い、`/session-end`を定義しています。まず`.claude/commands/session-end.md`を以下の内容で作成します。

```markdown
---
description: セッション終了時にresearchファイルを自動生成する
---

以下の処理を実行してください：

1. 本日の作業ログ（`logs/YYYY-MM-DD.md`）を読み込む
2. `python session_end_hook.py --log logs/YYYY-MM-DD.md` を実行する
3. 生成されたresearchファイルのパスを報告する
```

このファイルを設置すると、Claude Code上で`/session-end`コマンドが利用可能になります。コマンドが実行されると`session_end_hook.py`が呼び出され、以下の処理が連鎖的に走ります。

1. 当日のセッション記録（`.md`ファイル）を読み込む
2. 「記事化できる知見」のスコアリングを行う
3. スコアが閾値を超えた候補について、リサーチノートのテンプレートを自動生成する
4. `research/`ディレクトリに`YYYY-MM-DD-{slug}.md`形式で保存する

`session_end_hook.py`のClaude API呼び出し部分の実装例を以下に示します。

```python
# session_end_hook.py（抜粋）
import anthropic
import json

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY環境変数から自動取得

def call_claude(prompt: str, content: str) -> list[dict]:
    """Claude APIを呼び出してJSON形式のレスポンスを返す"""
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": f"{prompt}\n\n---\n{content}"
            }
        ]
    )
    return json.loads(message.content[0].text)

def extract_content_candidates(session_log: str) -> list[dict]:
    """セッションログから記事候補を抽出する"""
    prompt = """
    以下のセッション記録から、Zenn記事として価値のある知見を抽出してください。
    評価基準:
    - 具体的な実装上の課題と解決策があるか
    - 再現性があるか（読者が試せるか）
    - 独自性があるか

    以下のJSON形式で返してください:
    [{"title": "...", "score": 8.5, "summary": "..."}]
    """
    candidates = call_claude(prompt, session_log)
    return [c for c in candidates if c["score"] >= 7.0]
```

### researchファイルのテンプレート

生成されるresearchファイルには、以下のセクションが自動で埋められます。

- セッション知見サマリー
- キーワード（SEO観点で自動抽出）
- 競合分析の方向性
- ソース（セッション記録ファイルへの参照）

この時点ではまだ「素材」です。次のフェーズでこのファイルの保存を検知して、本格的な生成が始まります。

---

## フェーズ③ : watchdogによるファイル監視

```mermaid
stateDiagram-v2
    [*] --> 正常稼働

    正常稼働 --> Observerスレッド死亡検知 : スレッド異常終了

    Observerスレッド死亡検知 --> health_check失敗 : 死亡を検出

    health_check失敗 --> プロセス終了 : sys.exit(1)を実行

    プロセス終了 --> launchd再起動待機 : launchdが検知

    launchd再起動待機 --> 正常稼働 : プロセス再起動完了

    note right of 正常稼働
        Observerスレッド監視中
        パイプライン処理中
    end note

    note right of launchd再起動待機
        KeepAlive設定により
        自動で再起動
    end note
```

### asyncioとスレッドの橋渡し問題

最も実装で苦労したのが、 **watchdogのObserverスレッドとasyncioメインループのスレッド間安全通信** です。

watchdogの`FileSystemEventHandler`はバックグラウンドスレッドで動きます。一方、パイプラインの後続処理（API呼び出し、ファイル操作）はasyncioで書いています。この2つを直接つなぐと、スレッドセーフティの問題が発生します。

解決策は **`queue.Queue`を橋渡しに使う** パターンです。

```python
import asyncio
import queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# スレッド間共有キュー
event_queue = queue.Queue()

class ResearchFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.src_path.endswith(".md"):
            # スレッドセーフにキューへ積む
            event_queue.put(event.src_path)

async def pipeline_worker():
    """asyncioループからキューをポーリングする"""
    loop = asyncio.get_event_loop()
    while True:
        try:
            # run_in_executorでブロッキングのqueue.getをラップ
            file_path = await loop.run_in_executor(
                None,
                lambda: event_queue.get(timeout=1.0)
            )
            await run_pipeline(file_path)
        except queue.Empty:
            continue

def main():
    observer = Observer()
    observer.schedule(ResearchFileHandler(), path="./research", recursive=False)
    observer.start()

    asyncio.run(pipeline_worker())
```

`asyncio.Queue`ではなく`queue.Queue`を使い、`run_in_executor`でラップすることで、スレッドをまたいだ安全な受け渡しが実現できます。

### launchd KeepAliveの落とし穴

このウォッチャープロセスをmacOSのlaunchdで常駐させる際に、 **KeepAliveの検知漏れ** という罠がありました。

launchdはメインプロセスの死活監視はしますが、 **ワーカースレッド（Observerスレッド）が死んでもメインプロセスが生きていれば再起動しない** という仕様です。

```xml
<!-- launchd plist（抜粋） -->
<key>KeepAlive</key>
<true/>
```

これだけではObserverスレッドが例外で落ちてもlaunchdは気づきません。対策として、Observerスレッドの死活をメインスレッドから定期的にチェックし、死んでいれば`sys.exit(1)`でメインプロセスごと落とす実装を加えました。

```python
async def health_check(observer: Observer):
    """Observerスレッドの死活監視"""
    while True:
        await asyncio.sleep(30)
        if not observer.is_alive():
            print("Observer thread died. Exiting to trigger launchd restart.")
            sys.exit(1)
```

launchdに意図的にプロセスを落とさせてから再起動させる、という設計で安定運用できるようになりました。

---

## フェーズ④ : 3モデル協調による5段階生成

```mermaid
flowchart TD
    A[researchファイル入力] --> B

    subgraph PIPELINE["5段階生成パイプライン"]
        B["Stage 1\nQwen-Plus\n初稿生成"]
        C["Stage 2\nClaude Sonnet\n技術検証"]
        D["Stage 3\nGemini 1.5 Flash\n読みやすさ最適化"]
        E["Stage 4\nClaude Sonnet\nZennフォーマット整形"]
        F["Stage 5\nGemini 1.5 Flash\n最終推敲・slug生成"]

        B --> C --> D --> E --> F
    end

    F --> G{"DAレビュー\nClaude Sonnet\n品質評価"}

    G -->|"B-以上\n約85%"| H["Zenn自動公開"]
    G -->|"B-未満\n約15%"| I["手動レビューキュー"]
```

### なぜ3モデルを使うのか

1モデルで全部やれば安いのでは？という疑問はもっともです。この判断を裏付けるために、「1モデル（Claude Sonnet）で全ステージ処理」と「3モデル協調」をそれぞれ20記事ずつ生成し、DAレビュースコアで比較しました。

| 比較条件 | 平均グレード | B-以上通過率 |
|---------|------------|------------|
| Sonnet単体（5ステージ） | B | 70% |
| 3モデル協調（5ステージ） | A- | 85% |

通過率が15ポイント向上し、特に「技術的正確性」と「読みやすさ」のスコアが改善しました。各モデルの強みを活かした役割分担が有効に機能しています。

| ステージ | モデル | 役割 |
|---------|--------|------|
| Stage 1 | Qwen | 高速・低コストな初稿生成 |
| Stage 2 | Claude Sonnet | 技術的正確性の検証・補強 |
| Stage 3 | Gemini | 読みやすさ・構成の最適化 |
| Stage 4 | Claude Sonnet | Zennフォーマット整形・コードブロック確認 |
| Stage 5 | Gemini | 最終推敲・タイトル・slug生成 |

Qwenを初稿生成に使うのはコスト効率のためです。長い記事の粗削りな骨格を安く作り、その後のステージで高品質なモデルが肉付けと検証を行います。

### パイプラインの実装

各モデルの呼び出し関数（`call_qwen` / `call_sonnet` / `call_gemini`）の実装例を示します。

```python
import anthropic
import google.generativeai as genai
from openai import OpenAI  # Qwen は OpenAI互換APIで呼び出し

# 各クライアントの初期化
anthropic_client = anthropic.Anthropic()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
qwen_client = OpenAI(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
)

async def call_qwen(prompt: str, content: str) -> str:
    loop = asyncio.get_event_loop()
    def _call():
        response = qwen_client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": f"{prompt}\n\n---\n{content}"}],
            max_tokens=4096
        )
        return response.choices[0].message.content
    return await loop.run_in_executor(None, _call)

async def call_sonnet(prompt: str, content: str) -> str:
    loop = asyncio.get_event_loop()
    def _call():
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": f"{prompt}\n\n---\n{content}"}]
        )
        return message.content[0].text
    return await loop.run_in_executor(None, _call)

async def call_gemini(prompt: str, content: str) -> str:
    loop = asyncio.get_event_loop()
    def _call():
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(f"{prompt}\n\n---\n{content}")
        return response.text
    return await loop.run_in_executor(None, _call)
```

これらを使ったパイプライン実行フローについては、実装の詳細を次フェーズで説明します。

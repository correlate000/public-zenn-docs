---
title: "Claude Code Agent Teamsの実測値 — 4エージェントで3倍速は本当か"
emoji: "⚡"
type: "tech"
topics: ["ClaudeCode", "AI", "AgentTeams", "開発効率化", "並列処理"]
published: false
publication_name: "correlate_dev"
---

## はじめに

「AIエージェントを並列で動かすと3倍速くなる」という話を聞いたとき、あなたはどう感じましたか？

Hacker Newsに投稿されたある報告が、Claude Codeユーザーの間で静かな話題を呼んでいます。50,000行規模のコードベースに対して4エージェント・6タスクを並列実行したところ、処理時間が18〜20分から6分へと短縮された——つまり約3倍の速度向上が得られたというのです。

この数字は魅力的です。しかし同時に、いくつかの疑問も浮かびます。ファイルの競合はどう防ぐのか。トークン消費が4倍になるなら、コスト的に見合うのか。そして、ソロ開発者が日常的に使えるものなのか。

本記事では以下の問いを検証します。

- HNで報告された「3倍速」の背景と再現条件を整理できるか
- ファイル競合やコスト増といった実運用上の課題をどう乗り越えるか
- 4性格体制を用いた品質並列検証とタスク並列実行をどう組み合わせるか
- 「3倍速は本当か」という問いに、現時点でどう答えられるか

なお、本記事で参照する速度向上の数値は **筆者による独自再現テストではなく、HNユーザーによる報告値** です。再現条件・測定方法・ハードウェア環境は報告元の記載に基づいており、筆者が独立して検証したものではありません。この点をあらかじめ明記しておきます。

また、後述するcorrelate-workspaceは **筆者が開発・運営している個人プロジェクト** です。記事中での紹介は利益相反にあたる可能性があるため、ここに開示します。

執筆時点でZenn・Qiitaを調査した限り、この報告を日本語で詳しく取り上げた記事はほぼ見当たりません。本記事では、HNで報告された3倍速という数字の背景を整理しつつ、筆者が運営するcorrelate-workspaceの4性格体制（Pragmatist/Skeptic/Idealist/Connector）での実運用経験と照らし合わせながら、Claude Code Agent Teamsの実像に迫ります。

---

## HNで報告された3倍速の内訳

```mermaid
flowchart TD
    A([タスク開始]) --> B

    subgraph SEQ["シーケンシャル実行 : 18〜20分"]
        direction TB
        B[タスク1] --> C[タスク2]
        C --> D[タスク3]
        D --> E[タスク4]
        E --> F[タスク5]
        F --> G[タスク6]
    end

    G --> H{{"並列化への移行"}}

    H --> OV

    subgraph OV["オーバーヘッド要因"]
        direction TB
        OV1["調整コスト\n（エージェント間の同期）"]
        OV2["競合回避\n（ファイルロック・排他制御）"]
        OV3["オーケストレーション\n（タスク分配・監視）"]
    end

    OV --> PAR

    subgraph PAR["並列実行 : 6分（4エージェント × 6タスク）"]
        direction TB
        subgraph AG1["Agent 1"]
            T1[タスク1]
            T2[タスク2]
        end
        subgraph AG2["Agent 2"]
            T3[タスク3]
        end
        subgraph AG3["Agent 3"]
            T4[タスク4]
            T5[タスク5]
        end
        subgraph AG4["Agent 4"]
            T6[タスク6]
        end
    end

    PAR --> R([完了])

    R --> COMP

    subgraph COMP["結果比較"]
        direction LR
        C1["シーケンシャル\n18〜20分"]
        C2["並列実行\n6分"]
        C3["速度向上\n約3倍"]
        C1 -->|短縮| C2 -->|算出| C3
    end

    style SEQ fill:#ffecec,stroke:#e74c3c,color:#333
    style PAR fill:#ecf9ff,stroke:#2980b9,color:#333
    style OV fill:#fff8e1,stroke:#f39c12,color:#333
    style COMP fill:#eafaf1,stroke:#27ae60,color:#333
    style A fill:#2c3e50,color:#fff,stroke:#2c3e50
    style R fill:#27ae60,color:#fff,stroke:#27ae60
    style H fill:#8e44ad,color:#fff,stroke:#8e44ad
```

### 実験条件の詳細

Hacker Newsのスレッド（[Agent Teams関連議論](https://news.ycombinator.com/item?id=46902368)）で **HNユーザーが報告した値** は、以下の条件下で取得されています。再現条件・測定方法・ハードウェア環境の詳細は報告元に依存しており、筆者が独立して検証した数値ではありません。

- **コードベース規模**: 約50,000行（50k LOC）
- **エージェント数**: 4
- **タスク数**: 6（並列実行）
- **シーケンシャル実行時間**: 18〜20分
- **並列実行時間**: 約6分
- **速度向上倍率**: 約3倍

注目すべきは「4エージェントで3倍速」という非線形な関係です。単純な並列化であれば4倍近い速度向上が期待できますが、実際には3倍にとどまっています。この差分には、エージェント間の調整コスト、ファイルアクセスの競合回避、オーケストレーション層のオーバーヘッドが含まれていると考えられます。

### なぜ4倍にならないのか

```mermaid
graph TD
    A["アムダールの法則による速度向上分析"] --> B["処理構造の内訳"]
    A --> C["エージェント数と速度向上"]

    B --> D["逐次部分\n約11%\n並列化不可能"]
    B --> E["並列部分\n約89%\n並列化可能"]

    E --> F["4エージェント並列実行"]
    F --> G["理論速度向上\n最大 ≈ 3.56倍\n1 ÷ 0.11 + 0.89÷4"]

    C --> H["エージェント数: 1\n速度向上: 1.0倍（基準）"]
    C --> I["エージェント数: 2\n理論: 1.64倍"]
    C --> J["エージェント数: 4\n理論: 3.56倍\n実測: 3.0倍"]
    C --> K["エージェント数: 8\n理論: 4.71倍\n実測: 頭打ち傾向"]

    J --> L["差分: -0.56倍"]
    L --> M["損失要因"]
    M --> N["ファイル競合\nコンテキスト切替"]
    M --> O["タスク分割\nオーバーヘッド"]
    M --> P["調整・統合\nコスト"]

    G --> Q["実測速度向上\n約3.0倍\n18〜20分 → 6分"]
    Q --> R["理論値との乖離\n約0.56倍分のロス"]

    style A fill:#1a1a2e,color:#eee,stroke:#4a90d9
    style D fill:#c0392b,color:#fff,stroke:#e74c3c
    style E fill:#27ae60,color:#fff,stroke:#2ecc71
    style G fill:#2980b9,color:#fff,stroke:#3498db
    style Q fill:#8e44ad,color:#fff,stroke:#9b59b6
    style R fill:#e67e22,color:#fff,stroke:#f39c12
    style L fill:#e67e22,color:#fff,stroke:#f39c12
```

並列処理の効率を論じるうえで避けられないのが「アムダールの法則」です。どんなタスクにも、並列化できない逐次部分が存在します。エージェントへの指示配布、結果のマージ、コンフリクト解消といった工程は本質的に逐次的であり、これがボトルネックとなります。

アムダールの法則は次の式で表されます。

```
speedup = 1 / (s + (1 - s) / n)
```

ここで `s` は逐次部分の割合、`n` はエージェント数（並列数）です。HNの報告値（4エージェントで3倍速）をこの式に当てはめると、逐次部分の割合 `s` を逆算できます。

```
3 = 1 / (s + (1 - s) / 4)
s + (1 - s) / 4 = 1/3
4s + (1 - s) = 4/3
3s + 1 = 4/3
3s = 1/3
s ≈ 0.11
```

計算上は逐次部分が約11%と出ますが、実測の速度向上が理論値（4倍）に対して3倍にとどまっていることから、エージェント間の調整コストやオーケストレーションのオーバーヘッドが実効的な「逐次相当部分」として働いていると解釈できます。このコードベースでは **タスク全体の相当部分が直列依存または調整コストに費やされている** ということを意味します。

実際のコーディングタスクでも事情は同じです。依存関係のある複数ファイルを編集する場合、ある変更が完了するまで次の変更に着手できないケースが発生します。4エージェントを動かしていても、実質的に稼働しているのは常に4つとは限りません。

それでも「3倍速」という数字は実用上の意味を持ちます。18分かかっていた作業が6分で終われば、開発者の集中が途切れる前に結果を得られます。認知的なコストという観点からも、このレイテンシ削減は無視できません。

### Swarms vs Agent Teams：アーキテクチャの違い

```mermaid
graph TB
    subgraph FLAT["Swarmsフラット分散構造"]
        direction TB
        A1([エージェントA])
        A2([エージェントB])
        A3([エージェントC])
        A4([エージェントD])
        A1 <-->|直接通信| A2
        A2 <-->|直接通信| A3
        A3 <-->|直接通信| A4
        A4 <-->|直接通信| A1
        A1 <-->|直接通信| A3
        A2 <-->|直接通信| A4
    end

    subgraph HIER["Agent Teams階層構造"]
        direction TB
        ORC(["オーケストレーター\n（Claude Code）"])
        S1(["サブエージェント1\nPragmatist"])
        S2(["サブエージェント2\nSkeptic"])
        S3(["サブエージェント3\nIdealist"])
        S4(["サブエージェント4\nConnector"])
        ORC -->|タスク割り当て| S1
        ORC -->|タスク割り当て| S2
        ORC -->|タスク割り当て| S3
        ORC -->|タスク割り当て| S4
        S1 -->|結果報告| ORC
        S2 -->|結果報告| ORC
        S3 -->|結果報告| ORC
        S4 -->|結果報告| ORC
    end

    FLAT_CHAR["自律分散\n柔軟な通信経路\nファイル競合リスクあり\n調整コスト高"]
    HIER_CHAR["集中制御\nタスク競合回避\n品質並列検証\n約3倍速（報告値）"]

    FLAT --- FLAT_CHAR
    HIER --- HIER_CHAR

    style FLAT fill:#e8f4fd,stroke:#2196F3,stroke-width:2px
    style HIER fill:#f3e8fd,stroke:#9C27B0,stroke-width:2px
    style ORC fill:#9C27B0,color:#fff,stroke:#7B1FA2
    style A1 fill:#2196F3,color:#fff,stroke:#1565C0
    style A2 fill:#2196F3,color:#fff,stroke:#1565C0
    style A3 fill:#2196F3,color:#fff,stroke:#1565C0
    style A4 fill:#2196F3,color:#fff,stroke:#1565C0
    style S1 fill:#CE93D8,stroke:#9C27B0
    style S2 fill:#CE93D8,stroke:#9C27B0
    style S3 fill:#CE93D8,stroke:#9C27B0
    style S4 fill:#CE93D8,stroke:#9C27B0
    style FLAT_CHAR fill:#fff,stroke:#2196F3,stroke-dasharray:5 5
    style HIER_CHAR fill:#fff,stroke:#9C27B0,stroke-dasharray:5 5
```

HNでは同時期に、別のスレッド（[Swarms関連議論](https://news.ycombinator.com/item?id=46743908)）でもマルチエージェントのベンチマークが議論されていました。SwarmsとAgent Teamsはしばしば混同されますが、アーキテクチャ上の思想が異なります。

**Swarms** は、多数のエージェントがフラットな関係で協調するモデルです。中央オーケストレーターを持たず、エージェント同士が直接通信することを想定しています。スケーラビリティに優れる一方、調整のコストが分散します。

**Agent Teams（Claude Code）** は、オーケストレーター＋サブエージェントという階層構造を取ります。中央のオーケストレーターがタスクを分割し、各エージェントに割り当て、結果を統合します。制御が集中するため、複雑なタスクでも一貫性を保ちやすい特徴があります。

使い分けの目安を以下に示します。

| 観点 | Swarms | Agent Teams |
|------|--------|-------------|
| タスク規模 | 小〜中規模の独立タスクが多数 | 中〜大規模の統合が必要なタスク |
| タスク間依存 | 低い（疎結合） | 高い（密結合・順序制約あり） |
| 一貫性要件 | 低〜中（各エージェントが自律） | 高（オーケストレーターが品質を統括） |
| 向いているケース | データ収集・並列スクレイピング等 | コードリファクタリング・設計レビュー等 |

---

## ファイル排他制御の実践

### なぜこれが最大の落とし穴なのか

マルチエージェント実行の話になると、パフォーマンスより先に解決すべき問題があります。 **ファイルの競合** です。

複数のエージェントが同じファイルを同時に編集しようとすると、何が起きるか。最後に書き込んだエージェントの変更が残り、他の変更は消えます。あるいはマージの試みが失敗し、コードが破損します。並列化による速度向上の恩恵を受ける前に、この問題を解決しなければなりません。

### Git worktreesによる解決

```mermaid
sequenceDiagram
    participant O as オーケストレーター
    participant W1 as Worktree1<br/>（ブランチA）
    participant W2 as Worktree2<br/>（ブランチB）
    participant W3 as Worktree3<br/>（ブランチC）
    participant W4 as Worktree4<br/>（ブランチD）
    participant R as メインリポジトリ

    O->>R: メインブランチから分岐
    par worktreeの作成
        O->>W1: worktree作成（タスク1割当）
    and
        O->>W2: worktree作成（タスク2割当）
    and
        O->>W3: worktree作成（タスク3割当）
    and
        O->>W4: worktree作成（タスク4割当）
    end

    par 独立ブランチで並列作業
        W1->>W1: タスク1実装・コミット
    and
        W2->>W2: タスク2実装・コミット
    and
        W3->>W3: タスク3実装・コミット
    and
        W4->>W4: タスク4実装・コミット
    end

    W1-->>O: タスク1完了通知
    W2-->>O: タスク2完了通知
    W3-->>O: タスク3完了通知
    W4-->>O: タスク4完了通知

    Note over O: 依存関係を解析し<br/>マージ順を決定

    O->>R: ブランチA をマージ（依存なし）
    O->>R: ブランチB をマージ（依存なし）
    O->>R: ブランチC をマージ（A・B完了後）
    O->>R: ブランチD をマージ（C完了後）

    R-->>O: 全マージ完了
    Note over O,R: 処理時間 約1/3 に短縮
```

現時点でもっとも実践的な解決策は、 **Git worktrees** を使ったワークスペース分離です。

Git worktreesは、単一のリポジトリから複数の作業ツリーを作成する機能です。各エージェントに独立したworktreeを割り当てることで、ファイルシステムレベルでの競合を防ぎます。

```bash
# エージェント1用のworktree作成
git worktree add ../agent-1-workspace feature/api-refactor

# エージェント2用のworktree作成
git worktree add ../agent-2-workspace feature/test-coverage

# エージェント3用のworktree作成
git worktree add ../agent-3-workspace feature/docs-update

# エージェント4用のworktree作成
git worktree add ../agent-4-workspace feature/performance-fix
```

各エージェントは独自のブランチで作業し、完了後にオーケストレーターがマージを調整します。マージフェーズでは、依存関係の少ない順にブランチを統合するのが基本です。

```bash
# 依存関係の少ないブランチから順にmainへマージ
git checkout main
git merge feature/api-refactor        # 基盤となる変更を先に統合
git merge feature/test-coverage       # テストは実装依存のため次
git merge feature/docs-update         # ドキュメントは独立度が高い
git merge feature/performance-fix     # パフォーマンス修正を最後に

# コンフリクトが発生した場合は手動解消後にコミット
# git add . && git commit -m "Resolve merge conflicts"
```

マージ戦略（rebaseを使うか、マージコミットを残すか等）はチームのポリシーに応じて選択してください。このアプローチにより、並列実行中のファイル競合をほぼ完全に回避できます。

### タスク設計のポイント：依存関係の可視化

```mermaid
flowchart TD
    START([開始]) --> PHASE1[フェーズ1: 並列タスク群]

    PHASE1 --> T1["タスク1\nAPIエンドポイント実装\nエージェントA"]
    PHASE1 --> T2["タスク2\nUI コンポーネント作成\nエージェントB"]
    PHASE1 --> T3["タスク3\nテストスイート構築\nエージェントC"]
    PHASE1 --> T4["タスク4\nデータモデル設計\nエージェントD"]

    T1 --> SYNC{"フェーズ1\n完了待機\n同期ポイント"}
    T2 --> SYNC
    T3 --> SYNC
    T4 --> SYNC

    SYNC --> PHASE2[フェーズ2: 依存タスク群]

    PHASE2 --> T5["タスク5\n統合・結合処理\nエージェントA+B"]
    PHASE2 --> T6["タスク6\n品質検証・レビュー\nエージェントC+D"]

    T1 -.依存.-> T5
    T2 -.依存.-> T5
    T3 -.依存.-> T6
    T4 -.依存.-> T6

    T5 --> END_SYNC{"フェーズ2\n完了待機"}
    T6 --> END_SYNC

    END_SYNC --> RESULT(["完了\n18〜20分 → 約6分\n約3倍速"])

    style START fill:#4CAF50,color:#fff
    style PHASE1 fill:#2196F3,color:#fff
    style PHASE2 fill:#9C27B0,color:#fff
    style SYNC fill:#FF9800,color:#fff
    style END_SYNC fill:#FF9800,color:#fff
    style RESULT fill:#4CAF50,color:#fff
    style T1 fill:#E3F2FD,color:#000
    style T2 fill:#E3F2FD,color:#000
    style T3 fill:#E3F2FD,color:#000
    style T4 fill:#E3F2FD,color:#000
    style T5 fill:#F3E5F5,color:#000
    style T6 fill:#F3E5F5,color:#000
```

worktreesでワークスペースを分離しても、 **ロジカルな依存関係** は残ります。エージェントAがAPIのインターフェースを変更し、エージェントBがそのAPIを呼び出すコードを書いている場合、Bの作業はAの完了を待つ必要があります。

このため、タスク分割の前に依存関係グラフを作成することを推奨します。

```mermaid
graph TD
  T1[タスク1: データモデル定義<br/>エージェント1] --> T4[タスク4: APIエンドポイント実装<br/>エージェント1]
  T2[タスク2: ユーティリティ関数<br/>エージェント2] --> T5[タスク5: フロントエンド統合<br/>エージェント2]
  T3[タスク3: テストデータ準備<br/>エージェント3] --> T6[タスク6: 統合テスト<br/>エージェント4]
  T1 --> T5
  T4 --> T6
  T5 --> T6
```

フェーズ1（T1・T2・T3）は依存関係なしで並列実行できます。フェーズ2（T4・T5・T6）はフェーズ1の完了を待って順次開始します。依存関係のないタスクを先に並列実行し、後続タスクに向けてフェーズを分けることで、待機時間を最小化できます。

---

## correlate-workspaceの4性格体制との比較

### 4性格体制の概要

```mermaid
graph TD
    subgraph 入力
        TASK[タスク / 課題]
    end

    subgraph エージェント層
        PG["Pragmatist\n実行可能性・効率を重視\n現実的な解決策を提案"]
        SK["Skeptic\n批判的検証・リスク評価\n仮定や欠陥を洗い出す"]
        ID["Idealist\n理想形・革新的アイデア\n最善の姿を追求する"]
        CN["Connector\n統合・調整・橋渡し\n全体最適を図る"]
    end

    subgraph 評価・レビュー層
        PG_rev["Pragmatist が評価\n実装コスト・実現性チェック"]
        SK_rev["Skeptic が評価\n論理的整合性・欠陥検出"]
        ID_rev["Idealist が評価\n改善余地・ビジョン適合性"]
        CN_rev["Connector が統合評価\n矛盾調整・全体整合"]
    end

    subgraph 出力
        RESULT[統合アウトプット]
    end

    TASK --> PG
    TASK --> SK
    TASK --> ID
    TASK --> CN

    PG -->|提案出力| SK_rev
    PG -->|提案出力| ID_rev
    SK -->|検証出力| PG_rev
    SK -->|検証出力| ID_rev
    ID -->|アイデア出力| SK_rev
    ID -->|アイデア出力| PG_rev
    CN -->|調整出力| PG_rev
    CN -->|調整出力| SK_rev

    PG_rev --> CN_rev
    SK_rev --> CN_rev
    ID_rev --> CN_rev

    CN_rev --> RESULT
```

筆者が開発・運営しているcorrelate-workspaceでは、「4性格体制」と呼ぶ独自のマルチエージェント構成を採用しています（ **筆者はこのプロジェクトの開発者・運営者であり、利益相反の可能性があることを冒頭で開示しています** ）。各エージェントに異なる認知スタイルを持たせることで、コードレビューや設計決定の質を高めることを目的としています。

4つの役割は以下の通りです。

- **Pragmatist（実用主義者）**: 動くコードを最優先。「これで十分か」を問い続ける
- **Skeptic（懐疑論者）**: 前提を疑う。「本当にこれが必要か」を検証する
- **Idealist（理想主義者）**: ベストプラクティスを追求する。技術的負債を最小化したい
- **Connector（接続者）**: 全体の整合性を見る。各部分がどう繋がるかを把握する

### Adversarial Reviewの実践

```mermaid
sequenceDiagram
    participant Dev as 開発者
    participant SK as Skeptic
    participant ID as Idealist
    participant PR as Pragmatist
    participant CN as Connector

    Dev->>SK: コード変更を提出
    Note over SK: リスク・問題点の検証
    SK->>Dev: 懸念点・批判的フィードバック
    Dev->>Dev: フィードバックを反映

    Dev->>ID: 修正コードを提出
    Note over ID: 理想・可能性の評価
    ID->>Dev: 改善提案・ビジョンフィードバック
    Dev->>Dev: フィードバックを反映

    Dev->>PR: 修正コードを提出
    Note over PR: 実用性・実現可能性の評価
    PR->>Dev: 実装観点のフィードバック
    Dev->>Dev: フィードバックを反映

    Dev->>CN: 修正コードを提出
    Note over CN: 統合・整合性の評価
    CN->>Dev: 全体調整フィードバック
    Dev->>Dev: フィードバックを反映

    Note over Dev,CN: レビューサイクル完了
    Dev->>Dev: 最終コードを確定
```

4性格体制の核心は **adversarial review** にあります。コードの変更をSkepticが積極的に批判し、Idealistが改善案を提示し、Pragmatistがコストと効果を天秤にかけ、Connectorが全体への影響を評価します。

これは、一人の優秀なエンジニアが「自分のコードを自分でレビューする」作業とは根本的に異なります。認知バイアスのない多視点レビューにより、見落としを減らし、コード品質を引き上げることができます。

:::message
ドラフトはここで終端しています。本文の残りの節（コスト分析、まとめ等）は別途追記が必要です。
:::

---
title: "「What, not How」-- AIエージェント時代のプロンプト設計哲学"
emoji: "🧭"
type: "idea"
topics: ["claudecode", "ai", "promptengineering", "sdd"]
published: true
publication_name: "correlate_dev"
published_at: "2026-02-16 19:00"
slug: "what-not-how-prompt-philosophy"
---

## 導入: 優秀なシェフにレシピの手順を指示しますか？

ミシュラン三つ星のシェフに料理を頼むとき、「鍋に水を500ml入れて、中火にかけて、沸騰したら塩を小さじ1入れて......」と手順を逐一指示する人はいません。「今日は魚の旨味を活かした一皿をお願いします。アレルギーはエビです」と伝えれば、シェフは最適な調理法を自ら選びます。

AIエージェントとの関係も同じです。Claude CodeやCursorのようなエージェントは、すでに膨大なコーディング知識を持っている。にもかかわらず、多くのCLAUDE.mdやプロンプトは「手順書」のように書かれています。

```markdown
# Bad: How（手順）を書いている
1. まずgit statusを実行してください
2. 変更のあるファイルをgit addしてください
3. git commit -m "feat: ..." の形式でコミットしてください
4. git pushしてください

# Good: What（仕様）を書いている
- コミットメッセージはConventional Commits形式
- 1コミット1機能を原則とする
- mainブランチへの直接pushは禁止
```

前者はエージェントの判断力を信頼していません。後者は「守るべきルール」だけを伝え、具体的なコマンドの実行順序や状況判断はエージェントに委ねています。

この違いが「What, not How」という設計哲学の本質です。

CLAUDE.mdが300行を超えて肥大化する原因の多くは、「How」の記述にあります。手順書のような記述は一見丁寧に見えますが、トークンを浪費し、エージェントの自律的判断を阻害し、状況が変わるたびに書き直しが必要になります。

本記事では、この「What, not How」の哲学を体系的に論じます。なぜこの原則が重要なのか、どう実践するのか、そしてこれがAIエージェント時代の開発者にとってどんな意味を持つのかを考えていきます。

## 1. 宣言的プログラミングが教えてくれること

「What, not How」は新しい概念ではありません。コンピュータサイエンスには古くから宣言的プログラミングという考え方があり、多くの成功したツールがこの原則に基づいています。

| 宣言的（What） | 命令的（How） |
|:---:|:---:|
| SQL: `SELECT * FROM users WHERE active = true` | for文でusersをループし、active判定してフィルタ |
| React: `<UserList users={activeUsers} />` | DOMを手動操作してリストを構築 |
| Terraform: `resource "aws_instance" {...}` | AWS CLIコマンドを順番に実行 |
| CSS: `display: flex; gap: 16px;` | 各要素のposition/marginを個別計算 |

SQLが広く普及したのは、「20歳以上のユーザーを取得したい」という意図を書くだけで、データベースエンジンが最適なクエリプランを選んでくれるからです。インデックスの使い方やテーブルスキャンの戦略は、エンジンが状況に応じて判断する領域。

Reactが支持されたのは、「このデータをこのUIで表示したい」という状態とUIの対応関係を書くだけで、仮想DOMが最適な更新方法を選んでくれるからです。どうレンダリングを最適化するかは、フレームワークが担当します。

Terraformが採用されたのは、「こういうインフラ構成にしたい」というあるべき状態を書くだけで、プロバイダが現状との差分を計算して適用してくれるからです。

これらの成功事例には共通するパターンがあります。

1. 人間が「What」を定義する — 何が欲しいか、あるべき状態は何か
2. 実行エンジンが「How」を最適化する — 状況に応じて最善の実行方法を選ぶ
3. 環境が変わっても「What」は変わらない — Howは実行エンジンが適応する

AIエージェントは、汎用的な「実行エンジン」です。SQLのデータベースエンジンよりも、ReactのVirtual DOMよりも、はるかに広い範囲の「How」を自律的に判断できます。それなのに、CLAUDE.mdに手順書を書くということは、SQLに対して「まずテーブルAをフルスキャンして、次にインデックスBを使ってジョインして......」と書いているようなものです。

## 2. Spec-Driven Development (SDD) という潮流

この「What, not How」の考え方は、2025年に入りSpec-Driven Development（SDD: 仕様駆動開発）という名前で体系化されつつあります。

[Thoughtworksは2025年11月のTechnology Radar（Vol.33）にSDDをAssess（探索推奨）として掲載](https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development)。定義はシンプルです。「仕様（Specification）をAIエージェントのプロンプトとして活用し、コードを生成させる開発パラダイム」。

Googleのエンジニアリングリーダーで、Chrome DevToolsチームを率いた経験を持つAddy Osmaniは、「[How to write a good spec for AI agents](https://addyosmani.com/blog/good-spec/)」という記事で、より明確に原則を述べています。

> Focus on **What and Why, not How**

Osmaniが推奨する仕様の構成要素は以下の通りです。

- ゴール — 何を達成するか
- 成功基準 — 何をもって完了とするか
- 境界条件 — 何をしてはいけないか（Always do / Ask first / Never do）
- テスト計画 — どう検証するか

注目すべきは、この中に「実装手順」が含まれていないことです。手順は仕様ではなく、エージェントが自律的に決定するものだからです。

さらに、Martin Fowlerのサイトで[Birgitta Boeckelerが分析したSDDの3レベル分類](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)は、SDDの成熟度を理解するうえで重要です。

| レベル | 名前 | 仕様の役割 |
|:---:|:---|:---|
| 1 | **Spec-first** | 仕様を先に書き、AIがコードを生成 |
| 2 | **Spec-anchored** | 仕様をコード進化に合わせて維持・更新し続ける |
| 3 | **Spec-as-source** | 仕様自体がメインの成果物。コードは仕様から生成される「副産物」 |

「Spec-as-source」は、仕様がコードよりも長命になる世界です。コードは生成・破棄を繰り返しても、仕様は生き続ける。これは過激に聞こえるかもしれませんが、Claude Codeの文脈で考えると、すでにその萌芽が見えます。

- CLAUDE.md = プロジェクトの「Living Spec（生きた仕様）」
- Knowledge Files = ドメイン固有の仕様
- Commands = ワークフローの仕様
- Hooks = ガードレールの仕様

CLAUDE.mdを「設定ファイル」ではなく「仕様書」として捉え直すこと。それがSDD的なアプローチの第一歩です。

## 3. 「What, not How」を実践する3層構成パターン

理論はわかった。では実際にどう設計するのか。

私は合同会社コラレイトデザインの業務でClaude Codeを日常的に使っており、試行錯誤の末、以下の3層構成パターンにたどり着きました。

| 層 | 担当 | 記述するもの | 問い |
|:---|:---|:---|:---|
| **知識層** | Knowledge Files | ドメイン知識の仕様 | 「何を知っているか」 |
| **行動層** | Commands / Skills | ワークフローの仕様 | 「何をするか」 |
| **制約層** | Hooks | ガードレールの仕様 | 「何を守るか」 |

この3層すべてが「What」を記述しています。「How」はClaude Code（AIエージェント）が自律的に決定します。

### 知識層（Knowledge Files）: 「何を知っているか」

Knowledge Filesは、エージェントがドメイン知識を必要なときだけ参照するための仕組みです。

たとえば、BigQueryのスキーマやクエリパターンは、BigQuery操作時にだけ必要です。freee APIのトークン管理ルールは、freee連携時にだけ必要です。これらを常にCLAUDE.mdに書いておく必要はありません。

CLAUDE.mdにはルーティングテーブル（「いつ・何を読むか」の対応表）だけを置き、詳細な知識はKnowledge Filesに分離します。

```markdown
## Knowledge Files ルーティング
| トリガー | ファイル | 内容 |
|----------|---------|------|
| BigQuery操作時 | bigquery-patterns.md | スキーマ、冪等INSERT |
| Vercel操作時 | vercel-patterns.md | prebuiltデプロイ、OIDC |
| コード記述時 | coding-standards.md | 命名規則、Git運用 |
```

ここに書いているのは「このファイルにはこういう知識がある」というWhatです。「ファイルをどう読むか」「どのタイミングでメモリにロードするか」というHowはClaude Codeが判断します。

### 行動層（Commands / Skills）: 「何をするか」

Commandsは、繰り返し実行するワークフローを再利用可能な単位にしたものです。

たとえば、セッション開始時に毎回行うルーチン（前回セッションの確認、Daily Noteの作成、セッション記録ファイルの作成）を`/session-start`というCommandにしています。Commandに記述しているのは「セッション開始時に何をすべきか」であって、「bashコマンドをこの順番で実行せよ」ではありません。

```markdown
# /session-start
1. 前回セッションのNext Stepsを確認する
2. 今日のDaily Noteを確認/作成する
3. セッション記録ファイルを作成する
4. 今回のタスクをGoalとして宣言する
```

エージェントは状況に応じて、ファイルの存在確認方法や作成方法を自律的に判断します。前回セッションが存在しない場合、Daily Noteのテンプレートが変わった場合など、状況が変わってもCommandの記述を変更する必要がないのが宣言的設計の強みです。

### 制約層（Hooks）: 「何を守るか」

Hooksは、エージェントの行動に対する決定論的なガードレールです。

「What, not How」はエージェントに自律性を与えますが、「何をしてもよい」という意味ではない。越えてはならない境界を明確に設けることで、自律性と安全性を両立します。

たとえば、ファイル書き込み前に旧ドメイン名の使用をチェックするHookを設定しています。エージェントがどんなファイルをどんな方法で作成しても、旧ドメインが紛れ込んでいれば自動的に検出されます。

```
PreToolUse（Write/Edit）→ check-domain.sh → 旧ドメインが含まれていたら警告
```

Hookに記述しているのは「旧ドメインの使用を禁止する」という制約（What）です。「grepで検索してexit 1を返す」という実装詳細は、Hook自体のスクリプトが担う領域。エージェントのプロンプトには「何を守るか」だけが見え、「どう守るか」は仕組みの側が処理します。

## 4. 実践例: CLAUDE.md 300行→119行の削減

3層構成パターンを適用した結果を、数字で示します。

### Before: すべてをCLAUDE.mdに詰め込んでいた時代

私が最初に作成したCLAUDE.mdは15セクション、300行超の「全部入り」でした。作業者情報、セッション管理ルール、コーディング規約、Git運用、エラー対処方針、BigQueryパターン、freee API制約、FIRE基準...... あらゆる情報が1ファイルに詰まっていました。

問題は明白です。BigQuery操作をしている最中にfreee APIのルールがコンテキストを占有し、セッション管理の作業中にCSS命名規則がトークンを消費する。今の作業に関係ない情報が常にコンテキストウィンドウに居座る状態でした。

### After: 3層構成パターン適用後

| 要素 | Before | After | 削減率 |
|:---|:---|:---|:---|
| CLAUDE.md（グローバル） | 200+行 | 119行 | 約40% |
| CLAUDE.md（プロジェクト） | 300+行 | 109行 | 約64% |
| Knowledge Files | 0ファイル | 12ファイル（当時）→14ファイル（現在、736行） | -- |
| Commands | 0個 | 8個 | -- |
| Hooks | 0個 | 3個 | -- |

総行数は増えています。119 + 109 + 736 = 964行。元の500行より多い。しかし、ポイントは常時読み込まれるのはCLAUDE.mdの119行だけということです。Knowledge Filesの736行は、該当するトリガー条件に合致したときだけ読み込まれます。

これがContext Engineering（文脈全体の設計）の実践です。「一回のプロンプトをどう書くか」ではなく、「エージェントが必要とする文脈情報をどう構造化するか」を設計する。Prompt Engineeringの進化形として、[注目を集めている](https://www.thoughtworks.com/en-us/insights/blog/machine-learning-and-ai/vibe-coding-context-engineering-2025-software-development)概念です。

## 5. バイブコーディングとの決定的な違い

ここで一つ、重要な区別をしておきます。

2025年から流行した「バイブコーディング（Vibe Coding）」は、「なんとなくAIに指示して、なんとなく動くものを作る」アプローチです。「What, not How」と表面的に似ていますが、本質は全く異なります。

| 観点 | バイブコーディング | What, not How |
|:---|:---|:---|
| 仕様 | 曖昧、もしくは存在しない | 厳密に定義する |
| テスト | 「動けばOK」 | 成功基準を事前に定義 |
| 再現性 | 低い（同じ指示で違う結果） | 高い（仕様が同じなら結果も安定） |
| 品質保証 | 人間が目視確認 | 自動テスト + 境界条件で担保 |
| 対象 | プロトタイプ、個人実験 | 本番コード、チーム開発 |

「What, not How」は怠惰ではありません。むしろ「Whatの定義」に最大の知的労力を投じるアプローチです。

バイブコーディングが「料理を知らない人がシェフに"なんかいい感じの料理お願い"と言う」ことだとすれば、「What, not How」は「料理を熟知した人が"本日の魚を活かした前菜、アレルギーはエビ、コース全体のバランスを考慮して"と伝える」ことです。

「How」を書かないのは、手抜きではなく信頼です。ただし、その信頼は「Whatの厳密な定義」と「境界条件の明確化」によって裏打ちされています。

## 6. Context Engineeringへの架け橋

「What, not How」は、単なるプロンプトのテクニックではありません。これは、AIエージェントとの協働における設計哲学です。

2026年現在、この哲学はContext Engineeringという概念に合流しつつあります。

- Prompt Engineering — 「一回のプロンプトをいかにうまく書くか」
- Context Engineering — 「エージェントが必要とする文脈情報の全体設計」

Claude Codeの4要素（CLAUDE.md + Knowledge Files + Commands + Hooks）は、まさにContext Engineeringの実装です。一回のプロンプトではなく、エージェントが動作する環境全体を設計する。

[Anthropic公式のベストプラクティス](https://www.anthropic.com/engineering/claude-code-best-practices)が「CLAUDE.mdは簡潔に」「詳細はKnowledge Filesに分離」「Skillsで定型ワークフローをパッケージ化」「Hooksで決定論的な制御を追加」と推奨していることは、この文脈で理解すると腑に落ちます。公式が言っていることは、つまり「Whatを構造化し、Howはエージェントに任せよ」ということです。

そして、SDDの最終段階「Spec-as-source（仕様がソースになる）」が示唆する未来は、人間が書くのは仕様（What）だけで、コード（How）はAIが生成・保守する世界です。コードは使い捨てになり得るが、仕様は生き残る。この世界で開発者に求められるのは、「良い仕様を書く能力」という一点に集約されます。

## まとめ: 人間の仕事は「What」の定義に集中すること

AIエージェント時代の開発者の仕事は、変わりつつあります。

コードを書く時間は減り、仕様を定義する時間が増えていく。それは「楽になる」という意味ではなく、知的労力の配分が変わるということです。

- 「何が欲しいか」を明確に定義する（What）
- 「なぜそれが必要か」を言語化する（Why）
- 「何をしてはいけないか」の境界を設ける（Constraints）
- 「どう実現するか」はエージェントに委ねる（How → Agent）

最初の一歩は小さくて構いません。今のCLAUDE.mdを開いて、「これはWhatか？ Howか？」と問いかけてみてください。手順を書いている箇所があれば、それを「意図」に書き換えてみてください。

```markdown
# Before (How)
ファイルを保存したら、ターミナルでnpm run lintを実行し、
エラーがあれば修正してからgit addしてください。

# After (What)
- Lint通過を必須とする
- コミット前に自動でLintチェックを実行すること
```

たったこれだけの書き換えで、エージェントの自律性が上がり、CLAUDE.mdの保守コストが下がり、状況変化への適応力が高まります。

AIエージェントは優秀なシェフです。レシピの手順ではなく、どんな料理が食べたいかを伝えてください。

---

### 参考資料

https://www.anthropic.com/engineering/claude-code-best-practices

https://addyosmani.com/blog/good-spec/

https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html

https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices

https://addyosmani.com/blog/ai-coding-workflow/

https://www.thoughtworks.com/en-us/insights/blog/machine-learning-and-ai/vibe-coding-context-engineering-2025-software-development

### 関連記事

https://zenn.dev/correlate_dev/articles/claude-md-guide

https://zenn.dev/correlate_dev/articles/claude-code-knowledge-files

https://zenn.dev/correlate_dev/articles/ai-content-pipeline

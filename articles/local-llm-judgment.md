---
title: "Claude Code x ローカルLLM: いつ切り替えるべきかの判断基準"
emoji: "🤖"
type: "tech"
topics: ["llm", "claudecode", "ollama", "ai", "mac"]
published: true
published_at: "2026-02-12 08:00"
publication_name: "correlate_dev"
---

## Claude Max $200/月を払いながら、ローカルLLMを考えた

Claude Max Planを月額$200で契約しています。Claude Codeを使った開発は快適そのもの。複雑なコードベースの理解からマルチファイル編集、Agent Teamsによる並列作業まで、品質に不満はありません。

それでも「ローカルLLMを導入すべきタイミングはあるのか？」という疑問が頭から離れない。月$200は安くない金額だし、Mac mini M4 Pro 64GBという十分なスペックのマシンが手元にある。使わないのはもったいないのでは、と。

結論から言えば、ローカルLLMとクラウドLLMは競合ではなく補完関係にあります。この記事では、いつ・どのタスクでローカルLLMを使うべきかの判断基準を体系的に整理します。

## ローカルLLM最新事情（2026年2月）

2025年後半から2026年にかけて、ローカルLLM環境は劇的に進化しました。

### 実行環境の進化

| ツール | 特徴 | 向いている用途 |
|:--|:--|:--|
| **Ollama** | CLI/API中心、オープンソース、Claude Code連携対応 | 開発者、API経由の統合 |
| **LM Studio** | GUI中心、MLX最適化、初心者にも扱いやすい | プロトタイピング、モデル比較 |
| **llama.cpp** | 低レベル制御、最軽量 | カスタム用途、組み込み |

特にOllamaはv0.14.0でAnthropic Messages API互換を実装し、Claude Codeから直接ローカルLLMを呼べるようになった点が大きな転換点でしょう。

### 注目モデル

Qwen3-Coder-Nextが2026年のローカルLLM開発における最大のブレークスルーと言えます。

- 総パラメータ80B、活性パラメータわずか3B（MoE構成）
- SWE-Bench Verified: 70.6%（Claude Sonnet 4に迫る水準）
- Mac M4 Pro 64GBで100+ tokens/秒の推論速度
- 活性パラメータが3Bと軽量なため、比較的少ないメモリでも動作可能

その他にも、DeepSeek-R1の蒸留モデル（7B〜70B）やLlama 3系が選択肢に入ります。

:::message
モデルの性能は日進月歩で変化するため、ベンチマークスコアは記事公開時点（2026年2月）の値です。最新のスコアは[Ollama公式](https://ollama.com/library)や[SWE-Bench Leaderboard](https://www.swebench.com/)で確認してください。
:::

## Mac M4 Pro 64GBでの実力

筆者のメイン開発機であるMac mini M4 Pro（64GB）でのローカルLLM性能を整理します。

### フォーマット選択: MLX > GGUF

Apple Silicon上ではMLX形式のモデルを選ぶのがベストプラクティスです。

| 項目 | MLX | GGUF |
|:--|:--|:--|
| 推論速度 | 基準 | 約0.77倍（MLX比） |
| メモリ使用量 | 基準 | +約9GB |
| Apple Silicon最適化 | ネイティブ | 汎用 |

LM Studioを使う場合はMLXモデルが自動選択される場合が多く、Ollamaでも最近のバージョンではMLX対応が進んでいます。

### 動作可能なモデルの目安

Mac mini M4 Pro 64GBの場合、メモリ帯域幅273GB/sというスペックを活かせます。

| モデル | パラメータ | 必要メモリ目安 | 推論速度目安 |
|:--|:--|:--|:--|
| Qwen3-Coder-Next（80B-A3B） | 80B（活性3B） | 約20-25GB | 100+ tokens/s |
| DeepSeek-R1:14b | 14B | 約10GB | 60-80 tokens/s |
| Llama 3.3:70b-q4 | 70B（4bit量子化） | 約40GB | 20-30 tokens/s |
| Qwen2.5-Coder:32b | 32B | 約20GB | 40-60 tokens/s |

:::message alert
上記の推論速度はプロンプト長やバッチサイズ、同時実行プロセスによって大きく変動します。Agent Teams等で並列実行する場合、メモリ競合により性能が低下する可能性があるため注意が必要です。
:::

## 判断基準マトリクス: いつ切り替えるか

ここが本記事の核心部分。タスクの性質に応じて、Claude Code（クラウド）とローカルLLMのどちらを使うべきかを整理しました。

### 4つの判断軸

判断は以下の4軸で行います。

1. 推論の複雑さ -- タスクが高度な推論を必要とするか
2. プライバシー要件 -- データを外部に送信できるか
3. レイテンシ許容度 -- 応答速度がどの程度重要か
4. コスト感度 -- API利用枠の消費を抑えたいか

### タスク別 推奨環境マトリクス

| タスク | 推奨 | 理由 |
|:--|:--|:--|
| 複雑な設計・アーキテクチャ判断 | **Claude** | 推論品質が決定的に重要 |
| 大規模コードベースの理解 | **Claude** | 長いコンテキスト処理の精度 |
| マルチファイル編集・リファクタ | **Claude** | ファイル操作の信頼性 |
| Agent Teams（並列タスク） | **Claude** | ローカルLLMではメモリ競合 |
| セキュリティ監査・コードレビュー | **Claude** | 見落としのリスクを最小化 |
| 単純なコード補完・スニペット生成 | **ローカル** | 速度優先、品質は十分 |
| テンプレートからのファイル生成 | **ローカル** | 定型作業、パターンマッチ |
| 機密コード・社内コードの処理 | **ローカル** | データがローカルに留まる |
| オフライン環境での作業 | **ローカル** | ネットワーク不要 |
| 大量の単純変換バッチ処理 | **ローカル** | API制限なし、コスト$0 |
| ドキュメント生成（簡易） | **どちらでも** | タスクの複雑さ次第 |
| コミットメッセージ生成 | **どちらでも** | 品質要求が低い |

### コスト比較

Claude Max $200/月とローカルLLMのコストを比較してみましょう。

| 項目 | Claude Max | ローカルLLM |
|:--|:--|:--|
| 月額固定費 | $200（約30,000円） | $0 |
| 電気代（24時間稼働時） | - | 約500-1,000円/月 |
| 初期投資 | $0（既存PC利用） | $0（既存M4 Pro利用） |
| API制限 | Maxプラン枠内 | 制限なし |
| 推論品質 | 最高水準 | タスクによる |
| メンテナンス | 不要 | モデル更新・管理が必要 |

ポイントは、ローカルLLMはClaude Maxの「代替」ではなく「補助」という位置づけであること。$200/月のClaude Maxを解約してローカルLLMだけで開発するのは現実的ではありません。しかし、Claude Maxの利用枠を温存しつつ、単純なタスクをローカルに逃がすことには明確なメリットがある。

## ユースケース別の選択ガイド

### 1. コーディング補助（Copilot的な使い方）

結論として、ローカルLLMも検討に値します。

コード補完やスニペット生成のような「予測可能なパターン」の生成は、ローカルLLMの得意領域。Qwen3-Coder-Next（80B-A3B）なら、100+ tokens/sの速度でストレスなく補完が返ってきます。

ただし、IDE統合が必要な場合はContinue（VSCode拡張）やCopilot互換ツール経由でローカルLLMを接続する手間が発生する点に注意してください。

### 2. 設計・レビュー

結論として、Claude一択です。

「このアーキテクチャに問題はないか」「セキュリティ上の見落としはないか」といった判断には、推論品質が全てを決めます。ローカルLLMでセキュリティ監査をしたところで、重大な見落としがあれば本末転倒。

実際にMac M4 Proでqwen2.5-coder-32b + Clineを使った検証では、1時間以上の試行錯誤でもタスク完了に至らなかったケースが報告されています。同じタスクをClaude Sonnetに依頼したところ、5分で完了しました。

### 3. ドキュメント生成

結論として、タスクの複雑さ次第です。

READMEのテンプレート生成やCHANGELOG更新のような定型作業はローカルで十分。一方、技術設計書やAPI仕様書のような複雑なドキュメントはClaudeの推論品質が活きます。

### 4. プライバシー・オフライン

結論として、ローカル一択です。

社内の機密コード、顧客データを含むファイル、NDA下のプロジェクト。これらを外部APIに送信できない場面では、ローカルLLMが唯一の選択肢になります。

飛行機内やネットワークが不安定な環境での作業も同様。ローカルLLMの「常に手元にある」という特性は、クラウドAPIでは代替できない価値です。

## セットアップ: Ollama + Claude Code連携

ローカルLLMをClaude Codeから使うための最小手順を紹介します。

### 1. Ollamaのインストール

```bash
# macOS
brew install ollama

# または公式サイトからダウンロード
# https://ollama.com/download
```

### 2. モデルのダウンロード

```bash
# コーディング用途の推奨モデル
ollama pull qwen3-coder-next

# 汎用の小型モデル（軽いタスク用）
ollama pull qwen2.5:14b
```

### 3. Ollamaサーバーの起動

```bash
ollama serve
# デフォルトで http://localhost:11434 で起動
```

### 4. Claude Codeからの呼び出し

```bash
# 環境変数を設定してClaude Codeを起動
ANTHROPIC_BASE_URL="http://localhost:11434" \
ANTHROPIC_AUTH_TOKEN="ollama" \
claude --model qwen3-coder-next
```

永続的に設定する場合は、シェルの設定ファイルに追記します。

```bash
# ~/.zshrc に追加
export ANTHROPIC_BASE_URL="http://localhost:11434"
export ANTHROPIC_AUTH_TOKEN="ollama"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

:::message alert
`~/.zshrc` に直接書くと、全セッションでローカルLLMがデフォルトになり、Claude Max契約の意味がなくなります。通常利用（Anthropic API経由）に戻すには環境変数を解除する必要があるため、エイリアス方式を推奨します。
:::

```bash
# ~/.zshrc に追加
alias claude-local='ANTHROPIC_BASE_URL="http://localhost:11434" ANTHROPIC_AUTH_TOKEN="ollama" claude --model qwen3-coder-next'
alias claude-cloud='claude'  # デフォルト（Anthropic API）
```

### 現時点の制約

ローカルLLM経由でClaude Codeを使う場合、以下の制約があります。

- ファイル操作の信頼性が低い: モデルによってはJSON形式の文字列が返るだけでファイル作成が実行されないケースがある
- 長いコンテキストの精度低下: 32Kトークン以上のコンテキストでは応答品質が顕著に劣化
- Agent Teamsは非推奨: メモリ競合と推論品質の両面で課題がある
- 応答速度のばらつき: モデルやプロンプト長によって46秒〜3分以上かかることも

## 筆者の現状と結論

現在の筆者の開発環境と使い分けを整理します。

```
Mac mini M4 Pro 64GB
├── Claude Code（Claude Max $200/月）
│   ├── 複雑な設計・実装 → 主力
│   ├── Agent Teams → Claude一択
│   ├── コードレビュー → 品質重視
│   └── 記事執筆 → Claude（推論品質）
├── Ollama + Qwen3-Coder（ローカル）
│   ├── 単純なコード補完 → 速度優先
│   ├── テンプレート生成 → 定型作業
│   ├── 機密コード処理 → プライバシー
│   └── オフライン作業 → 唯一の選択肢
└── 使い分けの判断基準
    ├── 「これ、間違えたらまずい？」→ Claude
    ├── 「パターン通りでOK？」→ ローカル
    └── 「外に出せないデータ？」→ ローカル
```

Claude Maxの$200/月は、2026年2月時点でもまだ十分にペイする投資です。ローカルLLMは「Claude Maxの使用枠を節約する」「プライバシーが必要な場面をカバーする」という補完的な位置づけ。

判断に迷ったときのシンプルなルールとして、こう考えています。

> 「推論品質が結果に直結するタスクはClaude、パターンマッチで十分なタスクはローカル」

ローカルLLMの品質は急速に向上しており、Qwen3-Coder-NextのSWE-Bench 70.6%は1年前には考えられなかったスコア。2026年中に「ほとんどのタスクでローカルが実用的」になる可能性は十分にあります。今のうちに環境を整えておく価値はあるでしょう。

## まとめ

| 判断ポイント | Claude Code（クラウド） | ローカルLLM |
|:--|:--|:--|
| 推論品質が重要 | 最適 | 不足する場面あり |
| 単純なパターン生成 | オーバースペック | 十分 |
| プライバシー要件 | データが外部に出る | ローカルに留まる |
| オフライン | 不可 | 可能 |
| コスト | $200/月 | 電気代のみ |
| メンテナンス | 不要 | モデル更新が必要 |

ローカルLLMとクラウドLLMは「どちらが優れているか」ではなく「どう組み合わせるか」の問題。それぞれの強みを活かした使い分けこそが、2026年のAI開発における賢い選択だと考えています。

## 参考資料

https://ollama.com/

https://docs.ollama.com/api/anthropic-compatibility

https://lmstudio.ai/

https://qwenlm.github.io/blog/qwen3-coder/

https://qwen.ai/blog?id=qwen3-coder-next

https://qiita.com/SH2/items/39314152c0a6f9a7b681

https://blog.mosuke.tech/entry/2025/04/04/mac-mini-local-llm/

https://dev.classmethod.jp/articles/local-llm-guide-2026/

https://zenn.dev/correlate/articles/morning-bot-ai

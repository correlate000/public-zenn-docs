---
title: "コンテキストエンジニアリング入門：プロンプトの先にあるもの"
emoji: "🧠"
type: "tech"
topics: ["claude", "llm", "ai", "contextengineering"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「プロンプトエンジニアリング」という言葉が定着して久しいですが、2025年後半からAI界隈で急速に注目を集めている概念があります。それが **コンテキストエンジニアリング（Context Engineering）** です。

Shopify CEO の Tobi Lütke 氏が[2025年6月にX（旧Twitter）で「コンテキストエンジニアリングはプロンプトエンジニアリングよりも優れた用語だ」と発言](https://x.com/tobi/status/1935533422589399127)し、元OpenAIのAndrej Karpathy氏も「すべての本番LLMアプリにおいてコンテキストエンジニアリングが核心だ」と[支持を表明](https://x.com/karpathy/status/1937902205765607626)しました。

2025年9月29日、Anthropicがこの概念を[公式に定義・命名](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)し、1つの確立した技術領域として認知されるに至っています。

本記事では、コンテキストエンジニアリングの定義から実践パターン、そして2026年3月に発生したClaude Codeのソースリークで明らかになった内部実装まで、エンジニア視点で整理します。

## コンテキストエンジニアリングとは何か

Anthropicによる[公式定義（2025-09-29）](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)は以下のとおりです。

> "Context engineering is the set of strategies for curating and maintaining the optimal set of tokens (information) during LLM inference, including all the other information that may land there outside of the prompts."

日本語にすると「LLM推論中に、プロンプト以外のあらゆる情報を含む、最適なトークンセット（情報）を選定・維持するための戦略の集合体」となります。

Karpathyが示したメンタルモデルが直感的に理解しやすいです。すなわち **LLMをCPU、コンテキストウィンドウをRAM（ワーキングメモリ）** として捉える視点が核心。CPUにどのデータをRAMに乗せるかを設計するのがコンテキストエンジニアリングの本質です。

### プロンプトエンジニアリングとの違い

| 次元 | プロンプトエンジニアリング | コンテキストエンジニアリング |
|------|------------------------|--------------------------|
| 【スコープ】 | システムプロンプトの記述・最適化 | コンテキスト全体の設計・管理 |
| 【タイミング】 | 事前（静的） | 各推論ターンで動的に |
| 【対象】 | テキスト命令 | 命令 + ツール + 外部データ + 履歴 + メモリ |
| 【主な課題】 | 指示の曖昧さ | Context Rot（腐敗）、トークン効率 |
| 【実践の形】 | 単発のプロンプト改善 | アーキテクチャ設計・情報フロー設計 |

プロンプトエンジニアリングがテキスト命令の磨き込みだとすれば、コンテキストエンジニアリングはコンテキストウィンドウ全体を「何を、いつ、どんな順序で」入れるかを設計するアーキテクチャ的な営みです。

エージェント化が進むほど、コンテキストの蓄積・管理が不可避になります。システムプロンプト、ツール定義、MCPサーバー出力、外部データ、会話履歴。これらを総合的に管理する技術として、コンテキストエンジニアリングが浮上してきたのです。

## 5つの実践パターン

コミュニティで整理されてきたフレームワークをもとに、実践的な5つのパターンを紹介します。

### 1. CLAUDE.md ： プロジェクト知識の永続化

Claude Codeにおける `CLAUDE.md` は、プロジェクト固有の知識をAIエージェントに伝える最も基本的な手段です。適切に設計されたCLAUDE.mdは「右高度（Right Altitude）」が目標。過剰詳細でも過度に曖昧でもなく、柔軟性を保ちながら具体的なシグナルに基づく行動指針を提供します。

[HumanLayerのブログ](https://www.humanlayer.dev/blog/writing-a-good-claude-md)によると、CLAUDE.mdの命令遵守率は約70%です。300行未満（できれば200行以内）を推奨サイズとし、フロンティア思考LLMでも150〜200命令の遵守が合理的な上限とされています。

```markdown
# CLAUDE.md 推奨構造（~300行以内）
## 1. プロジェクト概要（必須コンテキスト）
## 2. アーキテクチャ・技術スタック
## 3. コーディング規約（linterで代替できないもののみ）
## 4. 禁止事項・注意点（MUST/NEVER形式）
## 5. よく使うコマンド
## 6. 詳細情報へのポインタ（コピーではなくパス参照）
```

**ポインタを使う、コピーしない**

CLAUDE.mdにコードやスキーマをそのまま貼り付けると、本体が更新されたときにCLAUDE.mdが陳腐化します。パス参照にすることで常に最新の状態を保てます。

```markdown
# ❌ スキーマをCLAUDE.mdに直接記載（陳腐化する）
## データベーススキーマ
CREATE TABLE users (id UUID PRIMARY KEY, ...)

# ✅ ファイルパス参照（常に最新）
## データベーススキーマ
スキーマ定義: `src/db/schema.ts`（line 1-50）
マイグレーション手順: `docs/database.md`
```

**禁止事項はNEVER/MUST形式で明記**

```markdown
## MUST Rules
- NEVER: 本番DBへの直接操作（ステージング経由必須）
- NEVER: APIキーのハードコード（環境変数を使用）
- MUST: git commit前にlint実行（`npm run lint`）
- MUST: 新機能はfeatureブランチで（main直接push禁止）
```

### 2. Hooks ： ルールの100%強制

CLAUDE.mdが「推奨（約70%遵守）」であるのに対し、Hooksは「強制（100%）」です。[2026年時点でのHooksの種類](https://www.humanlayer.dev/blog/writing-a-good-claude-md)は PreToolUse、PostToolUse、Stop、SessionStart の4種類で、PreToolUseはpermit/deny/ask/deferの4択制御が可能です。

```json
// settings.json の Hooks 設定例
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{
          "type": "command",
          "command": "~/.claude/hooks/validate-bash.sh"
        }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{
          "type": "command",
          "command": "~/.claude/hooks/run-linter.sh"
        }]
      }
    ],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/session-reminder.sh"
      }]
    }]
  }
}
```

**実装例: 本番環境保護フック**

```bash
#!/bin/bash
# validate-bash.sh: 本番DBへの直接アクセスをブロック
TOOL_INPUT=$(cat)
if echo "$TOOL_INPUT" | jq -r '.command' | grep -q "prod-db"; then
  echo '{"decision": "deny", "reason": "本番DBへの直接操作は禁止。staging経由で確認後に実行してください"}'
  exit 0
fi
echo '{"decision": "permit"}'
```

「絶対に守らせたいルール」はCLAUDE.mdではなくHooksに実装する。これがコンテキストエンジニアリングの重要な設計判断です。

### 3. MCP と Just-In-Time Retrieval

MCPサーバーを介した外部データ取得は、コンテキストエンジニアリングの中心的な手法の1つです。核心となる考え方が **Just-In-Time Retrieval（JIT取得）** です。

全データを事前にコンテキストに詰め込むのではなく、エージェントがファイルパスや識別子などの軽量ポインタを保持し、必要な瞬間にデータを動的ロードします。人間が「全部暗記」するのではなく「手帳で場所を調べる」認知スタイルに近い設計です。

ツール定義は30個を超えるとツール選択精度が最大3倍悪化するというデータがあります（[dbreunig](https://www.dbreunig.com/2025/06/26/how-to-fix-your-context.html)）。MCPサーバーのツール数は意識的に絞り込むことが重要です。

### 4. 3層メモリ設計

2026年3月31日、npm パッケージ `@anthropic-ai/claude-code` v2.1.88 に59.8MBのソースマップが誤包含されるという[ソースリーク](https://venturebeat.com/technology/claude-codes-source-code-appears-to-have-leaked-heres-what-we-know)が発生しました。Anthropicは「ヒューマンエラーによるパッケージングの問題。顧客データ・認証情報の漏洩なし」と声明を出しています。

公開された情報によると、Claude Codeは以下の3層メモリ構造を実装していることが判明しました（[MindStudio分析](https://www.mindstudio.ai/blog/claude-code-source-leak-memory-architecture)）。

```
Layer 1: MEMORY.md（軽量ポインタインデックス）
  - 常時コンテキストにロード
  - 1行 ≈ 150文字の形式
  - 上限: 200行 or 25KB

Layer 2: トピックファイル（詳細情報・JITロード）
  - MEMORY.mdが参照する詳細ファイル群
  - タスクが該当する場合のみロード

Layer 3: セッショントランスクリプト（実行ログ）
  - セッション単位の履歴
  - 参照頻度は低い
```

この設計をプロジェクトに応用するとこうなります。

```
MEMORY.md（常時ロード・軽量インデックス）
  ├── [Topic] API認証パターン → memory/api-auth.md
  ├── [Topic] データベース設計方針 → memory/db-patterns.md
  ├── [Failure] Case 1: XX実装でYYが壊れる → memory/failure-cases.md
  └── [Project] 現在の優先タスク → memory/current-tasks.md

memory/api-auth.md（詳細ファイル・必要時のみロード）
memory/db-patterns.md
memory/failure-cases.md
```

### 5. Progressive Disclosure（段階的開示）

[SwirlAI Newsletter](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)が提唱するProgressive Disclosureは、スキル/機能の情報を3段階に分けてコンテキストに開示する手法です。

```
Level 1: スキル名 + 1行説明（常時コンテキスト内）
    ↓ タスクが該当すると判断した場合のみ
Level 2: SKILL.md 全文（コア命令ロード）
    ↓ 特定シナリオで必要な場合のみ
Level 3: forms.md / reference.md 等（補足リソース）
```

常時ロードするのはメタデータだけに留め、必要なときだけ詳細を引き込む設計です。これにより、ベースラインのコンテキストコストを抑えながら、豊富な機能セットを維持できます。

## Context Rot の4分類と対策

コンテキストの品質劣化を指す[Context Rot（コンテキスト腐敗）](https://www.dbreunig.com/2025/06/22/how-contexts-fail-and-how-to-fix-them.html)は、[dbreunig](https://www.dbreunig.com/2025/06/22/how-contexts-fail-and-how-to-fix-them.html)が4種類に分類しています。

| 種別 | 症状 | 対策 |
|------|------|------|
| 【Poisoning（汚染）】 | 幻覚が蓄積・増幅する | 定期的なコンパクション、事実確認ツール |
| 【Distraction（散漫）】 | 同じ行動を繰り返す | Context Pruning、タスク完了時のクリア |
| 【Confusion（混乱）】 | 無関係なツールを使用する | Tool Loadout最適化、JIT Retrieval |
| 【Clash（矛盾）】 | 相反する指示で停止する | Context Quarantine（独立スレッド化）|

長いコンテキストでトランスフォーマーのパフォーマンスが低下する原因の多くはこのContext Rotです。単に「長いコンテキストに対応している」というスペックではなく、 **コンテキストの質を維持する設計** こそが重要です。

### コンパクション（Context Compaction）

コンテキストウィンドウが上限に近づいた際に会話を要約し、新しいコンテキストウィンドウで継続する手法です。単純に「古いものを切る」のではなく、アーキテクチャ上の決定・未解決のバグ・重要な前提を保持しながら、重複したツール出力などを廃棄する判断が核心になります。

## Claude Codeソースリークで判明した内部設計

前述のソースリークをdbreunigが詳細に[分析した記事（2026-04-04）](https://www.dbreunig.com/2026/04/04/how-claude-code-builds-a-system-prompt.html)から、Claude Codeのシステムプロンプトキャッシュ戦略が明らかになっています。

公開された情報によると、`SYSTEM_PROMPT_DYNAMIC_BOUNDARY` というマーカーが存在し、これを境にキャッシュ戦略が切り替わります。

```
[静的ゾーン]（全組織・全セッションでグローバルキャッシュ）
  組み込み命令 / ツール定義 / 動作ルール
  ─────────────────────────────────────
  SYSTEM_PROMPT_DYNAMIC_BOUNDARY（モデルには見えない区切り）
  ─────────────────────────────────────
[動的ゾーン]（セッション固有キャッシュ）
  CLAUDE.md / git status / 現在日時 / 実行環境
```

この設計には経済的な意図があります。[Claude APIのプロンプトキャッシュ](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)はキャッシュヒット時にコストが90%削減。静的ゾーンを全ユーザー・全セッションでグローバルキャッシュすることで、Anthropicは大規模なAPIコストを節約できます。キャッシュヒット1回で損益分岐点に達するため、2回目以降は確実な節約になります。

また、ソースリーク内には **KAIROS** というコードネームのデーモンモード（現時点では未リリース・フラグ無効状態）が150回以上参照されていることも判明しています（[DeepLearning.AI報告](https://www.deeplearning.ai/the-batch/claude-codes-source-code-leaked-exposing-potential-future-features-kairos-and-autodream/)）。GitHubウェブフックを購読し、定期的なtickプロンプトを受け取る常時稼働型バックグラウンドエージェントモード。同様に **autoDream** という、ユーザーのアイドル時間に「メモリ統合」を行うフォーク型サブエージェントの存在も確認されています。これらは将来の機能として開発中のものであり、現在のClaude Codeには存在しません。

## 実践チェックリスト

コンテキストウィンドウを最適化するための確認項目をまとめます。

```
[ ] CLAUDE.md: 300行未満、2,000トークン未満
[ ] MCP tools 合計: 20,000トークン未満
[ ] ベースラインコンテキスト: 20,000トークン未満（200kの10%を目標）
[ ] コンパクション頻度: 60,000トークンごと以内
[ ] ツール定義数: 30未満（超える場合はJIT選択を検討）
[ ] 100%遵守が必要なルール: Hooksへ移行済み
[ ] Few-Shotサンプル: 多様性優先、網羅性は不要
[ ] MEMORY.md: 1行150文字以内のポインタ形式、上限200行
```

## まとめ

コンテキストエンジニアリングをひと言で表すなら「**LLMに何を渡すかの設計**」です。

- CLAUDE.mdは「推奨」、Hooksは「強制」。用途を使い分ける
- コンテキストに詰め込まない。JITで動的にロードする
- Context Rotの4種類を意識し、事前に対策を設計する
- 3層メモリ設計で「常時ロード」と「JITロード」を分離する

プロンプトエンジニアリングの時代が「何を言うか」の最適化だったとすれば、コンテキストエンジニアリングの時代は「何を、いつ、どんな形で渡すか」のアーキテクチャ設計です。

AIエージェントが長期タスクを担い始めた今、コンテキストの設計はシステムアーキテクチャと同じ重みを持つ技術判断になりつつあります。

---

**参考リンク**

- [Effective context engineering for AI agents ： Anthropic Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [How Contexts Fail and How to Fix Them ： dbreunig](https://www.dbreunig.com/2025/06/22/how-contexts-fail-and-how-to-fix-them.html)
- [How Claude Code Builds a System Prompt ： dbreunig](https://www.dbreunig.com/2026/04/04/how-claude-code-builds-a-system-prompt.html)
- [Writing a Good CLAUDE.md ： HumanLayer](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
- [Agent Skills: Progressive Disclosure ： SwirlAI](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)
- [Claude Code Source Leak: Memory Architecture ： MindStudio](https://www.mindstudio.ai/blog/claude-code-source-leak-memory-architecture)

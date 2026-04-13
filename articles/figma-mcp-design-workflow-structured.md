---
title: "Figma MCP × Claude Codeでデザインワークフローを構造化する方法"
emoji: "🏗️"
type: "tech"
topics: ["figma", "mcp", "claudecode", "ai", "design"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Figma MCP と Claude Code の組み合わせは、デザイン作業の可能性を大きく広げます。しかし「AIにデザインを任せたら毎回品質がバラバラ」という問題に直面する方も多いはずです。

実際に使い始めると、同じ指示を出しても生成結果が安定しません。プロンプトを少し変えただけで全く異なるレイアウトが出てきたり、ブランドカラーが無視されたりします。

本記事では、AI生成デザインの品質ブレを抑える2つの手法を紹介します。

1. **Constraint-First プロンプトテンプレート ** — 5段構成で制約を先行定義する
2. **10ラウンド品質ゲート ** — 生成→検証→反復を数値基準で管理する

どちらも実際のワークフロー設計の中で試行錯誤して確立したものです。

---

## なぜ AI デザインは品質がブレるのか

Claude Code に「ログイン画面を作って」と指示すると、毎回異なるデザインが生成されます。これは設計上の特性で、LLM は確率的にテキストを生成するため、同じ入力でも出力が変わります。

デザイン生成においてブレの原因になるのは主に以下の3つです。

**1. 制約の曖昧さ **
「シンプルなデザインで」「モダンな感じで」といった主観的な指示は、モデルによって解釈が大きく異なります。

**2. コンテキストの不足 **
デザインシステムのトークン、対象ユーザー、使用環境などの情報がないと、汎用的なデザインが出力されます。

**3. 反復の欠如 **
1回の生成で完成を求めると、品質の担保が難しくなります。

---

## Constraint-First プロンプトテンプレート

品質ブレを抑える最も効果的な手法は、 ** 制約を先に定義すること ** です。

「何を作るか」より「何に従うか」を先に伝えることで、生成の自由度を適切に絞ります。

### 5段構成のテンプレート

```
## [1] 制約（Constraints）
- カラーパレット: Primary #0066FF / Surface #F8F9FA / Text #1A1A1A
- タイポグラフィ: 見出し 24px Bold / 本文 16px Regular / ラベル 12px Medium
- スペーシング: 4の倍数（4px, 8px, 16px, 24px, 32px）
- コンポーネント: Figma ライブラリ内の既存コンポーネントを優先使用
- アクセシビリティ: コントラスト比 4.5:1 以上を必須とする

## [2] ターゲット（Target）
- ユーザー: 40代〜60代の行政職員
- デバイス: デスクトップ優先（1280px）
- 操作環境: マウス操作、タッチ操作なし

## [3] 仕様（Specification）
- 画面: ログインフォーム
- 必須要素: メールアドレス入力、パスワード入力、ログインボタン、パスワードリセットリンク
- 状態: デフォルト / フォーカス / エラー / ローディングの4状態

## [4] 参照（Reference）
- 既存コンポーネント: Button（Primary）, Input（Text）, Link（Inline）
- ブランドガイドライン: コーポレートロゴは左上、フォームは中央配置

## [5] 成果物（Deliverable）
- Figma フレーム: 1440 × 900
- 命名規則: Login / [State] の形式
- 自動レイアウト: 有効にすること
```

### なぜこの順番か

制約（Constraints）を最初に置くことで、モデルはその後の内容を制約フィルター越しに処理します。仕様を先に書いてしまうと、制約が「追加条件」として処理されやすく、無視されるケースが増えます。

実際にこのテンプレートを使ったところ、制約なしのプロンプトと比べてカラーパレット遵守率が大きく改善しました。

---

## 10ラウンド品質ゲートの設計

プロンプトを構造化するだけでは、複雑な画面の品質を担保しきれません。 ** 生成→検証→反復 ** の3ステップを、明確な数値基準で繰り返す仕組みが必要です。

### ラウンド構成

| ラウンド | フォーカス | 品質ゲート（通過基準） |
|---|---|---|
| R0 | 基盤構造 | フレームサイズ・グリッド・自動レイアウト設定 |
| R1 | 骨格レイアウト | 主要エリアの配置、余白の一貫性 |
| R2 | タイポグラフィ | フォントサイズ・ウェイト・行間の仕様準拠 |
| R3 | カラーシステム | パレット外の色が0件であること |
| R4 | コンポーネント | ライブラリ参照率 80% 以上 |
| R5 | 状態管理 | 全インタラクティブ要素の全状態が揃っていること |
| R6 | アクセシビリティ | コントラスト比 4.5:1 以上（全テキスト） |
| R7 | スペーシング | 4px グリッドからの逸脱が0件 |
| R8 | 命名・整理 | レイヤー命名規則、グルーピングの整合性 |
| R9 | 総合検証 | 全ゲート通過、スペック出力の完全性 |

### ラウンドの進め方

各ラウンドの指示例です。

```
## R3: カラーシステム検証

以下の観点でFigmaフレームを確認し、問題があれば修正してください。

検証項目:
- 使用色がカラーパレット（Primary #0066FF / Surface #F8F9FA / Text #1A1A1A）以外のものを含んでいないか
- ホバー状態・無効状態に使用している色がパレットから派生しているか
- ボーダー・シャドウの色がシステム外の値になっていないか

通過基準: パレット外の色が0件

修正後、使用している全カラー値のリストを出力してください。
```

### 品質ゲートを数値化する意味

「良いデザインかどうか」は主観的な判断ですが、「カラーパレット外の色が何件あるか」は数値で測定できます。

ゲートを数値化することで2つのメリットがあります。

- **AI への指示が明確になる ** ：「改善してください」より「パレット外の色を0件にしてください」の方が修正精度が上がります
- ** 進捗が見える ** ：どのラウンドでどの問題が解消されたかを追跡できます

---

## 実装例：ワークフロー全体のスクリプト

実際のセッションで使っている、ラウンドを管理するプロンプトの骨格です。

```markdown
# Figma デザインセッション

## セッション設定
- ファイルID: [FIGMA_FILE_ID]
- 対象フレーム: [FRAME_ID]
- 現在のラウンド: R[N]

## 完了ラウンド
- [x] R0: 基盤構造（✅ 2026-02-24 完了）
- [x] R1: 骨格レイアウト（✅ 2026-02-24 完了）
- [ ] R2: タイポグラフィ ← 現在のラウンド

## 制約（全ラウンド共通）
[Constraint-First テンプレートをここに貼付]

## R2 の指示
タイポグラフィ仕様に従って全テキストを確認・修正してください。
通過基準: 仕様外のフォントサイズが0件

修正完了後、使用した全テキストスタイルの一覧を出力してください。
```

このマークダウンをそのままClaude Code に貼り付けて使います。セッションをまたいでも状態を引き継げます。

---

## Figma MCP のツールとワークフローの対応

各ラウンドで主に使うFigma MCPのツールです。

```
generate_figma_design  → R0〜R1（初期生成、レイアウト構築）
update_figma_design    → R2〜R8（各要素の修正・調整）
get_figma_data         → 各ラウンドの検証（現状把握）
get_node_by_id         → ピンポイントの要素確認
```

修正フェーズ（R2以降）では `generate_figma_design` より `update_figma_design` を優先します。既存フレームを一から作り直すと、前のラウンドで積み上げた修正が消えるためです。

---

## よくある失敗パターン

### ラウンドをスキップしたくなる

「R3〜R5はまとめてやろう」という誘惑があります。しかし複数の問題を同時に修正すると、原因の特定が難しくなり、デバッグに時間がかかります。

ラウンドを細かく分けているのは、 ** 問題を分離して管理するため ** です。

### 制約より仕様を先に書いてしまう

「ログイン画面を作りたい」というゴールを先に書き、その後に「でも色は#0066FFで…」と付け加える書き方になりがちです。

制約ブロックを常に先頭に置く習慣をつけることが重要です。テンプレートをスニペットとして登録しておくと便利です。

### 1回の指示で完璧を求める

「全部一気に作って」という指示は、ほぼ失敗します。R0から始めて段階的に品質を上げる設計を守ってください。

---

## まとめ

Figma MCP × Claude Code のデザインワークフローを安定させるには、以下の2つが鍵です。

**1. Constraint-First プロンプト **
- 制約→ターゲット→仕様→参照→成果物の5段構成
- 制約を最初に定義することで、生成の自由度を適切に絞る

**2. 10ラウンド品質ゲート **
- 生成→検証→反復を数値基準で管理
- 各ラウンドに明確な通過基準を設ける

AIによるデザイン生成は「一発で完成させる」ものではなく、「段階的に品質を上げていく」プロセスです。この認識の転換が、実用的なワークフロー設計の起点になります。

---

## Constraint-First テンプレートのスニペット化

毎回テンプレートを手書きしていては時間がかかります。シェルのエイリアスやスニペットとして登録しておくと、呼び出しが素早くなります。

### シェルスニペット（.zshrc または .bashrc）

```bash
# Figma MCP Constraint-First テンプレートをクリップボードにコピー
alias figma-prompt='cat << "EOF" | pbcopy
## [1] 制約（Constraints）
- カラーパレット: Primary #0066FF / Surface #F8F9FA / Text #1A1A1A
- タイポグラフィ: 見出し 24px Bold / 本文 16px Regular / ラベル 12px Medium
- スペーシング: 4の倍数（4px, 8px, 16px, 24px, 32px）
- コンポーネント: Figmaライブラリ内の既存コンポーネントを優先使用
- アクセシビリティ: コントラスト比 4.5:1 以上を必須とする

## [2] ターゲット（Target）
- ユーザー: [ユーザー層を記入]
- デバイス: [デスクトップ/モバイル/両方]
- 操作環境: [マウス/タッチ/両方]

## [3] 仕様（Specification）
- 画面: [画面名を記入]
- 必須要素: [要素リストを記入]
- 状態: [状態リストを記入]

## [4] 参照（Reference）
- 既存コンポーネント: [使用するコンポーネント名を記入]

## [5] 成果物（Deliverable）
- Figmaフレーム: [サイズを記入]
- 命名規則: [命名パターンを記入]
- 自動レイアウト: 有効にすること
EOF
echo "テンプレートをクリップボードにコピーしました"'
```

### プロジェクト固有テンプレートをファイルで管理する

プロジェクトごとにデザインシステムが異なる場合は、テンプレートをファイルで管理すると一元管理できます。

```bash
# テンプレートファイルを作成
mkdir -p .claude/figma-templates
touch .claude/figma-templates/constraint-base.md
```

```markdown
<!-- .claude/figma-templates/constraint-base.md -->
## [1] 制約（Constraints）
- カラーパレット: Primary #0066FF / Accent #FF6600 / Surface #F8F9FA / Text #1A1A1A / Border #E0E0E0
- タイポグラフィ:
  - Display: 32px / Bold / line-height 1.2
  - Heading: 24px / SemiBold / line-height 1.3
  - Body: 16px / Regular / line-height 1.6
  - Caption: 12px / Medium / line-height 1.4
- スペーシングスケール: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64
- 角丸: sm=4px / md=8px / lg=12px / xl=16px
- シャドウ: sm=0 1px 3px rgba(0,0,0,0.1) / md=0 4px 12px rgba(0,0,0,0.1)
- コンポーネント: Button / Input / Card / Badge / Alert を優先使用
- アクセシビリティ: コントラスト比 4.5:1 以上（本文）/ 3:1 以上（大見出し）
```

```bash
# テンプレートを読み込んで Claude Code に渡す
cat .claude/figma-templates/constraint-base.md | pbcopy
```

---

## 品質ゲートの自動チェックスクリプト

各ラウンドの検証を半自動化するシェルスクリプトの例です。Figma MCP ツールの呼び出しは Claude Code 側で行いますが、ラウンド状態の管理をスクリプトで補助できます。

```bash
#!/bin/bash
# figma-round-check.sh — デザインラウンドの進捗管理スクリプト

SESSION_FILE=".claude/figma-session.json"
ROUNDS=("R0:基盤構造" "R1:骨格レイアウト" "R2:タイポグラフィ" "R3:カラーシステム" \
        "R4:コンポーネント" "R5:状態管理" "R6:アクセシビリティ" \
        "R7:スペーシング" "R8:命名整理" "R9:総合検証")

# セッションファイルの初期化
init_session() {
  local file_id=$1
  local frame_id=$2
  cat > "$SESSION_FILE" << EOF
{
  "fileId": "$file_id",
  "frameId": "$frame_id",
  "currentRound": 0,
  "completedRounds": [],
  "startedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  echo "セッションを初期化しました: $SESSION_FILE"
}

# 現在のラウンド状態を表示
show_status() {
  if [ ! -f "$SESSION_FILE" ]; then
    echo "セッションファイルが見つかりません。init を実行してください。"
    return 1
  fi
  
  local current=$(python3 -c "import json; d=json.load(open('$SESSION_FILE')); print(d['currentRound'])")
  local completed=$(python3 -c "import json; d=json.load(open('$SESSION_FILE')); print(','.join(map(str, d['completedRounds'])))")
  
  echo "=== Figma デザインセッション状態 ==="
  for i in "${!ROUNDS[@]}"; do
    local round_info="${ROUNDS[$i]}"
    if echo "$completed" | grep -q "\b$i\b"; then
      echo "  ✅ $round_info"
    elif [ "$i" -eq "$current" ]; then
      echo "  ▶  $round_info ← 現在のラウンド"
    else
      echo "  ⬜ $round_info"
    fi
  done
}

# ラウンドを完了としてマーク
complete_round() {
  local round=$1
  python3 << EOF
import json
with open('$SESSION_FILE', 'r+') as f:
    data = json.load(f)
    if $round not in data['completedRounds']:
        data['completedRounds'].append($round)
    data['currentRound'] = $round + 1
    f.seek(0)
    json.dump(data, f, indent=2)
    f.truncate()
print(f"R$round を完了としてマークしました")
EOF
}

case "$1" in
  init) init_session "$2" "$3" ;;
  status) show_status ;;
  complete) complete_round "$2" ;;
  *) echo "使い方: $0 {init <file_id> <frame_id>|status|complete <round_number>}" ;;
esac
```

使い方：

```bash
# セッション開始
bash figma-round-check.sh init XXXXXXXXXXXXXXXX "0:1"

# 状態確認
bash figma-round-check.sh status

# R0 完了としてマーク
bash figma-round-check.sh complete 0
```

---

## トラブルシューティング

### ラウンドを進めると前のラウンドの修正が戻る

**原因**: `generate_figma_design` を修正フェーズで使っているため、フレームが再生成されています。

**対処**: R2以降は必ず `update_figma_design` を使ってください。セッション管理マークダウンにツールを明示することで防げます。

```
## R4 の指示
【使用ツール: update_figma_design のみ。generate は使用禁止】

以下のコンポーネントをライブラリ参照に変更してください:
...
```

### 品質ゲートの数値基準を満たせないラウンドで止まる

ラウンドによっては、数値基準を完全に達成することが困難な場合があります。その場合は「例外事項」として記録し、次のラウンドに進む判断をします。

```
## R6 例外事項（2026-02-24）
- ヘルパーテキスト（12px グレー）のコントラスト比: 3.8:1（基準: 4.5:1）
- 例外理由: デザインシステムの既存仕様と整合性を保つため
- 対応方針: デザインシステム改定時に合わせて修正
```

例外事項を明示することで、品質ゲートの意図（問題の特定と記録）は維持されます。

### 複数画面に同じワークフローを適用したい

セッション管理マークダウンをテンプレート化し、画面ごとにコピーして使います。

```bash
# テンプレートから新しい画面用セッションを作成
cp .claude/figma-templates/session-template.md .claude/sessions/login-form-session.md
sed -i '' 's/\[FIGMA_FILE_ID\]/ACTUAL_FILE_ID/g' .claude/sessions/login-form-session.md
sed -i '' 's/\[FRAME_ID\]/ACTUAL_FRAME_ID/g' .claude/sessions/login-form-session.md
```

---

## 参考

- [Figma Developer Docs: MCP Server](https://developers.figma.com/docs/figma-mcp-server/)
- [Figma Blog: Introducing Claude Code to Figma](https://www.figma.com/blog/introducing-claude-code-to-figma/)

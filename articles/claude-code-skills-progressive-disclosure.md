---
title: "Claude Code Skills設計パターン — 段階的開示とコンテキスト2%ルール"
emoji: "🧩"
type: "tech"
topics: ["claudecode", "claude", "ai", "promptengineering", "devproductivity"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Claude Code Skillsの入門記事は日本語でも増えてきましたが、「どう設計すれば良いか」の判断基準を体系化した記事はまだ少ない印象です。

本記事では、15以上のSkillsを実運用してきた経験から、以下の設計思想を整理します。

- ** 段階的開示（Progressive Disclosure） ** ：必要な情報を必要なタイミングで展開する
- ** コンテキスト2%ルール ** ：Skillsのdescription設計の定量的基準
- **3層構造 ** ：SKILL.md / agents / references によるコンテキスト分離
- **Iron Law + 合理化防止 ** ：品質を担保するパターン

---

## Skills設計の核心：段階的開示とは

### 問題：全てを書くとコンテキストが溢れる

Claude Codeはセッション開始時に `~/.claude/CLAUDE.md`、プロジェクトの `.claude/CLAUDE.md`、そして全Skillsの `description` を読み込みます。

Skillsのdescriptionの合計サイズは ** コンテキストウィンドウ全体の約2%** （最大16,000文字程度）を占めます。この2%は避けがたい固定コスト。Skillsが増えるほど、実際の作業コンテキストに使えるトークンが減る計算になります。

```
コンテキストウィンドウ（200K tokens）
├── システムプロンプト（固定）
├── CLAUDE.md（固定）
├── Skills descriptions（固定、最大〜16,000文字）← ここが問題
├── 会話履歴（可変）
└── 実際の作業内容（可変）←← これが削られる
```

### 解決策：段階的開示

段階的開示（Progressive Disclosure）は、UIデザインの概念をコンテキストエンジニアリングに応用したものです。

```
Level 1: description（常時ロード）
  ↓ Skillが呼ばれたとき
Level 2: SKILL.md（Skill実行時にロード）
  ↓ 複雑な処理が必要なとき
Level 3: agents / references（処理内でロード）
```

descriptionは「このSkillが何をするか」を20〜50文字で伝えるだけにします。詳細な手順は `SKILL.md` に書き、Skill実行時に初めてロードします。

---

## Skills設計の3層構造

### 実際のディレクトリ構成

```
~/.claude/skills/
├── session-start.md     ← Skill本体（description + 手順）
├── session-end.md
├── da-review.md
├── content-scout.md
├── agents/              ← サブエージェント定義
│   ├── pragmatist.md
│   ├── skeptic.md
│   ├── idealist.md
│   └── connector.md
└── references/          ← 詳細リファレンス（必要時のみ参照）
    ├── da-review-criteria.md
    ├── session-template.md
    └── content-formats.md
```

### Layer 1: descriptionの設計

```markdown
<!-- ~/.claude/skills/da-review.md の冒頭部分 -->
# da-review

**description**: Runs a Devil's Advocate (Skeptic-only) critical review.
Use for quick single-perspective review before commit or deploy.
Triggered by: "/da-review", "DAレビュー", "レビューして".

<!-- この後に詳細な手順を書く -->
```

descriptionのルール：

1. **1〜2文で完結 ** ：トリガーとユースケースのみ
2. ** 英語推奨 ** ：日本語より約30%トークンを節約できる
3. ** トリガーフレーズを必ず含める ** ：Claude Codeがどこでこのスキルを使うか判断するため

### Layer 2: SKILL.md（Skill本体）

```markdown
# da-review Skill

## 概要

Devil's Advocate（懐疑派）の視点から、コードや記事の批判的レビューを実行する。
コミット前・デプロイ前の最終チェックに使う。

## 実行手順

### Step 1: レビュー対象の特定
ユーザーのメッセージから以下を特定する：
- 対象ファイル（またはコードブロック）
- レビューの深度（quick / thorough）

### Step 2: 懐疑派の視点でレビュー

以下の観点でレビューを実施：

** セキュリティ（Security） **
- SQLインジェクション / XSS / CSRF の脆弱性
- 認証・認可の抜け漏れ
- シークレットのハードコード

** データ整合性（Data Integrity） **
- トランザクション処理の正確性
- エラー時のロールバック
- 冪等性の担保

** エラーハンドリング（Error Handling） **
- 全例外の握りつぶしがないか
- ユーザーへのエラーメッセージが適切か

<!-- ... 続く ... -->
```

### Layer 3: agents / references（処理内でロード）

複雑な処理では、サブエージェントの定義を別ファイルに切り出します。

```markdown
<!-- ~/.claude/skills/agents/skeptic.md -->
# Skeptic（懐疑派）エージェント定義

あなたはDevil's Advocateです。以下の特性を持ちます：

- 全ての前提に疑問を持つ
- 「最悪のケース」を想定する
- 楽観的な見通しに反証を示す
- 具体的な失敗シナリオを提示する

## 発言スタイル

「〇〇という前提が成立しない場合は？」
「この実装が本番環境で壊れるとしたら、何が原因か？」
「レビュアーが見落としているリスクは？」
```

```markdown
<!-- ~/.claude/skills/da-review.md の中でロードを指示 -->
## サブエージェントの読み込み

懐疑派の視点でレビューする際は、以下を参照：
`~/.claude/skills/agents/skeptic.md`
```

---

## コンテキスト2%ルールの実践

### description文字数の管理

```bash
# 全Skillのdescription文字数を確認するスクリプト
#!/bin/bash

SKILLS_DIR=~/.claude/skills
TOTAL=0

echo "Skill                        | 文字数"
echo "-----------------------------+-------"

for skill_file in "$SKILLS_DIR"/*.md; do
  skill_name=$(basename "$skill_file" .md)
  
  # description行を抽出（最初のコードブロック外の段落）
  desc=$(awk '/^## /{exit} /^[^#]/{print}' "$skill_file" | \
    head -5 | tr -d '\n')
  
  char_count=${#desc}
  TOTAL=$((TOTAL + char_count))
  
  printf "%-30s | %d\n" "$skill_name" "$char_count"
done

echo "-----------------------------+-------"
echo "Total: $TOTAL characters"
echo "Estimated tokens: $((TOTAL / 4)) (÷4 approximation)"
```

### 実測データ（15 Skills）

自社の15 Skillsを計測した結果：

| カテゴリ | Skills数 | 合計文字数 | 推定トークン |
|---|---|---|---|
| セッション管理 | 3 | 1,240 | 310 |
| レビュー・品質 | 4 | 2,100 | 525 |
| コンテンツ | 5 | 1,870 | 468 |
| 経理・広告 | 3 | 980 | 245 |
| 合計 | 15 | 6,190 | 1,548 |

16,000文字の上限に対して6,190文字（約39%）で収まっています。この余裕があることで、新しいSkillを追加したときの影響を吸収できます。

---

## Iron Law：合理化を防ぐ品質パターン

GitHubで39,700 Star（2026年3月時点）を獲得している [superpowers](https://github.com/superpowers) リポジトリには「Iron Law」という概念があります。

AIが作業中に「例外的に〇〇していい」と自己判断して品質基準を下げることを防ぐパターンです。

### 合理化防止テーブルの例

```markdown
<!-- ~/.claude/skills/da-review.md の中に記載 -->

## 合理化防止テーブル

以下の思考パターンが発生した場合、即座に作業を止めてユーザーに確認する：

| 合理化パターン | 正しい対応 |
|---|---|
| 「小さな変更だから品質チェックを省略してもいい」 | 省略しない。チェックは必須 |
| 「ユーザーが急いでいるからレビューを簡略化しよう」 | 急いでいても手順は変えない |
| 「この部分は確認済みだから飛ばしていい」 | 全ての手順を実行する |
| 「このファイルは影響範囲が小さいから」 | 影響範囲の判断はユーザーに委ねる |

## Iron Law

以下のルールは例外なく適用される：

1. セキュリティチェックは全項目を実施する（スキップ禁止）
2. 「HIGH」評価の指摘は修正が完了するまでレビューを完了としない
3. ファクトチェックなしに事実主張を含む文章を承認しない
```

### 実装への応用

```markdown
<!-- 開発系Skillの合理化防止例 -->

## 絶対ルール（Iron Law）

以下は「効率化」「時間短縮」を理由にしても破れない：

- `git push --force` は禁止（`--force-with-lease` を提案する）
- 本番DBへの直接操作は禁止（ステージング経由必須）
- APIキーのハードコードは禁止（環境変数化を提案する）
- セキュリティレビュー未実施のコードをコミットしない

「ユーザーが急いでいる」「小さな変更」「確認済み」はいずれも例外理由にならない。
```

---

## トリガーフレーズの設計

### 日英両方のトリガーを設定する

```markdown
**Triggered by:**
- "/da-review"
- "/DAレビュー"
- "DAレビュー"
- "レビューして"
- "コードレビュー"
- "デプロイ前にチェック"
- "review this"
- "check this code"
```

日本語のトリガーを追加することで、日常会話の中でSkillが自然に発火します。

### モデル指定のパターン

```markdown
<!-- Skillでモデルをopusかsonnetかhaikusで指定する -->

## 使用モデル

このSkillは **claude-opus-4** で実行する。

理由：セキュリティレビューの見落としは手戻りコストが大きいため、
精度最優先でopusを選択する。

<!-- haiku向けSkillの例 -->
## 使用モデル

このSkillは **claude-haiku** で実行する。

理由：テンプレートへの値の埋め込みは単純タスクのため、
速度・コスト優先でhaikuを選択する。
```

---

## disable-model-invocationの活用

Skillsの中でサブエージェントを呼び出す場合、`disable-model-invocation` フラグを使うと、Skill実行中のモデル切り替えを防げます。

```markdown
## 実行設定

- disable-model-invocation: true

このフラグを設定することで、Skill実行中にユーザーが別のモデルを
指定しても無視される。品質ゲートの確実な実行に有効。
```

---

## 実運用からの知見

### 15 Skillsの運用で学んだこと

**1. Skillは小さく保つ **

1つのSkillに複数の責務を持たせると、どちらの用途にも使いにくくなります。`session-start` と `session-update` は分離した方が使いやすかった実例です。

```markdown
<!-- NG: 1つのSkillに全部詰め込む -->
# session-management
セッション開始・更新・終了を全て管理する

<!-- OK: 責務を分割する -->
# session-start    ← セッション開始専用
# session-update   ← 作業中の更新専用
# session-end      ← 終了・自動抽出専用
```

**2. descriptionにuse whenを明記する **

いつ使うSkillかを明示することで、Claude Codeの自律的なSkill選択が改善します。

```markdown
**Use when**: コミット前、デプロイ前の最終チェック時。
  成果物（コード/記事）の品質確認に使う。

**Do NOT use when**: 仕様検討段階、アイデア出しフェーズ。
  実装済みのコードや完成した文章に対してのみ使う。
```

**3. 失敗ケースを明示する **

何が失敗とみなされるかを書くことで、曖昧な完了基準を防ぎます。

```markdown
## 完了の定義

以下が全て満たされた場合のみ「完了」とする：
- [ ] 全セキュリティ項目にHIGHが0件
- [ ] 型チェックがエラー0件
- [ ] テスト通過率100%
- [ ] ファクトチェック済み（記事の場合）

1つでも未達の場合は「完了」とせず、修正を促す。
```

---

## テンプレート：新規Skill作成時の雛形

```markdown
# {skill-name}

## description（50文字以内を目標）

{このSkillが何をするかを1〜2文で記述。トリガーフレーズを含める。}

**Triggered by**: "/{skill-name}", "{日本語トリガー}", "{英語トリガー}"
**Use when**: {どんな状況で使うか}
**Do NOT use when**: {使ってはいけない状況}
**Model**: {claude-opus-4 / claude-sonnet-4 / claude-haiku} — {理由}

---

## 実行手順

### Step 1: {最初のステップ名}
{具体的な処理内容}

### Step 2: {次のステップ名}
{具体的な処理内容}

---

## 合理化防止テーブル

| 合理化パターン | 正しい対応 |
|---|---|
| {よくある例外理由} | {正しい対応} |

## Iron Law（絶対ルール）

- {例外なく守るルール1}
- {例外なく守るルール2}

---

## 完了の定義

- [ ] {完了条件1}
- [ ] {完了条件2}
```

---

## まとめ

Claude Code Skillsの設計で重要な原則をまとめます。

| 原則 | 具体的な実践 |
|---|---|
| 段階的開示 | descriptionは20〜50文字、詳細はSKILL.md |
| コンテキスト2%ルール | description合計を16,000文字以内に収める |
| 3層構造 | SKILL.md / agents / references で分離 |
| Iron Law | 合理化防止テーブルで品質を担保 |
| 日英トリガー | 日常会話からの自然な発火を実現 |

Skillsは「プロンプトを再利用する仕組み」ではなく、「AIの振る舞いを設計する仕組み」です。段階的開示の思想を取り入れることで、コンテキスト消費を抑えながら高品質な作業を実現できます。

### 関連記事

- [Claude CodeのCLAUDE.mdで作業品質を担保する](https://zenn.dev/correlate_dev/articles/claude-md-guide)
- [Claude Code Knowledge Filesでチーム知識を外部化する](https://zenn.dev/correlate_dev/articles/claude-code-knowledge-files)
- [Claude Code Parallel Agentsで複数タスクを同時処理する](https://zenn.dev/correlate_dev/articles/claude-code-parallel-agents)

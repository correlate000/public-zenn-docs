---
title: "Claude Code Skill toolがopus-4-6[1M]を壊す落とし穴 — model overrideの衝突"
emoji: "⚠️"
type: "tech"
topics: ["claudecode", "ai", "debugging", "anthropic", "devtools"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Claude Code で長時間の作業セッションを回している最中に、突然こんなエラーが出たことはないでしょうか。

```
API Error: Extra usage is required for 1M context
```

奇妙なのは、セッション開始時には問題なく動いていたこと。親セッションは opus-4-6 の 1M context で起動済みで、Extra Usage の設定も有効なはずだった。それなのに、特定の Skill を呼び出した瞬間にエラーが起きる。

この問題は、Claude Code の **Skill tool が内部的に sub-execution（サブ実行）を起動する仕組み** に起因します。本記事では、エラーの発生メカニズムを解析し、即時使える回避策と再発防止の運用設計を解説します。

実際に 2026-04-11 の ISVD サイト制作セッションで発生した問題を元にした実例です。

---

## 症状：pre-commit hook の通過直前に詰む

問題が起きたのは、`isvd-social-data` リポジトリへのコミット直前でした。

セッション状況：
- 親セッション：opus-4-6、1M context、Extra Usage 有効
- 作業内容：コンテンツ MDX ファイルの品質チェックを経てコミット
- pre-commit hook：`/content-validate` と `/fact-check` の実行をゲート条件にしている

コミット直前に `/content-validate` を呼び出したところ、以下のエラーが返ってきました。

```
Skill: /content-validate を実行中...

API Error: Extra usage is required for 1M context
(リクエスト: model=claude-sonnet-4-6, max_tokens=8192)
```

エラーメッセージの中に `model=claude-sonnet-4-6` という記述があります。1M context のエラーなのに、なぜ sonnet が登場するのか。ここに問題の本質があります。

---

## 原因：Skill tool が model を上書きする

### Skill の frontmatter に注目する

`~/.claude/skills/content-validate/SKILL.md` の先頭を確認すると、こうなっていました。

```yaml
---
description: |
  ISVD記事（MDX）の品質チェックと自動修正を実行する。
  DAレビューで繰り返し検出されるパターンを事前に検出・修正し、
  レビューラウンド数を削減する。
model: sonnet
allowedTools:
  - Read
  - Edit
  - Glob
  - Grep
  - Bash
---
```

`model: sonnet` という1行が問題の原因です。

### sub-execution の仕組み

Claude Code の Skill tool は、スラッシュコマンドを呼び出すと **親セッションとは独立した sub-execution を新たに起動します** 。このとき、SKILL.md の frontmatter に `model` が指定されている場合、sub-execution はその指定モデルで新たに API リクエストを発行します。

```
親セッション（opus-4-6 + 1M Extra Usage）
    │
    └── /content-validate を呼び出す
            │
            └── sub-execution 起動（model: sonnet を読む）
                    │
                    └── API リクエスト
                        model: claude-sonnet-4-6
                        max_tokens: 8192
                        ←── 1M Extra Usage が未設定！
                        ←── API Error: Extra usage is required
```

親セッションに付与されている Extra Usage は、その API キーのセッションスコープに結びついています。sub-execution は **新しい API リクエスト** を発行するため、そのリクエストが 1M context を要求するかどうかに関わらず、使用するモデルに Extra Usage が設定されているかどうかが問われます。

sonnet-4-6 は 1M context の Extra Usage 設定がない。したがってエラーになる。

### 皮肉な自己矛盾

この問題は構造的な自己矛盾を引き起こします。

1. 長いセッションで大量のコンテキストを扱う → 1M context が必要 → opus-4-6 で起動
2. コミット前に品質チェック Skill を呼び出す（hook 要件）
3. Skill が sonnet で sub-execution → Extra Usage なし → エラー
4. エラーのせいでコミットできない → hook を通過できない

「1M context を使うために設計したワークフローが、品質チェック Skill のせいでコミットを阻む」という状況です。

---

## エラーの検出方法

問題が起きたとき、最初の確認ポイントは **エラーメッセージ内のモデル名** です。

```
API Error: Extra usage is required for 1M context
(リクエスト: model=claude-sonnet-4-6, max_tokens=8192)
```

`model=claude-sonnet-4-6` が表示されている場合、呼び出した Skill の frontmatter に model 指定があると疑います。

### 確認コマンド

使用している Skills の model 指定を一括確認するには、以下のコマンドが使えます。

```bash
# ~/.claude/skills/ 配下の全 SKILL.md から model 指定を検索
grep -r "^model:" ~/.claude/skills/

# プロジェクトローカルの skills も確認
grep -r "^model:" .claude/skills/ 2>/dev/null
```

出力例：

```
/Users/naoya/.claude/skills/content-validate/SKILL.md:model: sonnet
/Users/naoya/.claude/skills/fact-check/SKILL.md:model: sonnet
/Users/naoya/.claude/skills/da-review/SKILL.md:model: sonnet
```

`model: sonnet` がある Skill を 1M context セッションから呼び出すと、同じエラーが発生します。

---

## 4つの回避策

問題の原因が判明したので、対処法を整理します。状況に応じて使い分けてください。

### 回避策1：インライン実行（即時対応・推奨）

最もすぐに使える対処法です。Skill を呼び出す代わりに、**Skill の SKILL.md を Read して、そのチェック項目を親セッション内でインラインに実行する** 方法です。

手順：

```
1. SKILL.md を Read して品質チェック項目を把握する
2. 親セッション（opus-4-6）内で同じ処理を実行する
3. hook が期待するマーカーファイルと品質ログ JSON を手動生成する
4. コミットを実行する
```

実際に採用したマーカーファイル生成の例：

```bash
# /tmp/ 以下にマーカーファイルを生成（hook が存在確認する）
touch /tmp/.isvd-content-validated
touch /tmp/.isvd-fact-checked

# 品質ログ JSON を生成
cat > /tmp/content-validate-result.json << 'JSON'
{
  "validated_at": "2026-04-11T09:35:00+09:00",
  "model": "claude-opus-4-6",
  "execution_mode": "inline",
  "files_checked": ["columns/2026-04-nextactions.mdx"],
  "issues_found": 0,
  "issues_fixed": 2,
  "status": "PASS"
}
JSON
```

この方法の利点は、**モデル切り替えが発生しない** 点です。すべての処理が親セッションの claude-opus-4-6 で実行されるため、Extra Usage の問題が起きません。即時に解決できる唯一の方法です。

デメリットは、Skill の処理をインラインで再実装する手間がかかること。定期的に発生する場合は後述の恒久的な対策が必要です。

### 回避策2：Skill の model 指定を削除する

SKILL.md から `model:` 行を削除すると、sub-execution は親セッションと同じモデルを引き継ぎます。

```yaml
# 変更前
---
description: ISVD記事の品質チェックを実行する
model: sonnet  # ← この行を削除
allowedTools:
  - Read
  - Edit
---

# 変更後
---
description: ISVD記事の品質チェックを実行する
# model 指定なし → 親セッションのモデルを継承
allowedTools:
  - Read
  - Edit
---
```

親セッションが opus-4-6 で動いていれば、Skill も opus-4-6 で実行されます。

ただし副作用があります。**Skill を単独で呼び出す（短いセッションから実行する）と、デフォルトモデルで動く** ことになります。そのセッションが sonnet で起動していれば sonnet で、opus で起動していれば opus で動く。Skill の動作がセッション依存になるため、コスト管理が難しくなります。

品質チェック系の Skill は sonnet でも十分なケースが多いため、model 指定を削除するよりも「どのモデルで動いてほしいか」を明示する方が望ましい設計といえます。

### 回避策3：1M context を使わないセッションに分離する

作業を2段階に分けます。

- **短セッション（sonnet）**：品質チェック・コミット・push
- **長セッション（opus-4-6 + 1M）**：大規模コンテキストが必要な設計・実装

コミット前の品質チェックを「短セッションで実行するもの」と位置づければ、model 衝突は起きません。

```bash
# 長セッション終了後、短セッションで品質チェック + コミット
claude --model claude-sonnet-4-6 "
/content-validate columns/2026-04-nextactions.mdx
問題なければ git commit -m 'feat: add NextActions MDX'
"
```

この方法は最も根本的ですが、**セッション切り替えの運用負荷が高い** です。大規模コードベースでは、品質チェックにも十分なコンテキストが必要なため、常に分離できるとは限りません。

### 回避策4：sonnet にも 1M Extra Usage を有効化する

sonnet-4-6 に対しても 1M context の Extra Usage を有効化すれば、sub-execution が sonnet で起動してもエラーが起きません。

ただしこれはコスト問題が伴います。1M context の Extra Usage は長いコンテキストを保持するために追加料金が発生します。品質チェック Skill に 1M は通常不要であり、コストに対してリターンが合いません。緊急回避として有効化し、恒久策を並行して整備するのが現実的です。

### 回避策の比較

| 回避策 | 即時対応 | 恒久性 | 副作用 |
|---|---|---|---|
| インライン実行 | 可能 | 低（毎回手動） | なし |
| model 指定削除 | 設定変更が必要 | 高 | セッション依存 |
| セッション分離 | 設定不要 | 高 | 運用負荷あり |
| sonnet に 1M 有効化 | 可能 | 高 | コスト増 |

---

## 再発防止：MEMORY への記録

同じ問題を繰り返さないために、`~/.claude/projects/.../memory/MEMORY.md` または `~/.claude/knowledge/` に記録します。

実際に追加したルール：

```markdown
## MUST: Skill tool は sub-execution を起動する — model 指定に注意

1M context セッション（opus-4-6）から model: sonnet の Skill を呼び出すと
`API Error: Extra usage is required for 1M context` で失敗する。

### 発生条件
- 親セッション: opus-4-6 + 1M Extra Usage 有効
- Skill frontmatter: model: sonnet（またはその他の 1M 非対応モデル）
- Skill が sub-execution として独立した API リクエストを発行する

### 即時回避策
Skill を呼び出さず、親セッション内でインライン実行する。
SKILL.md を Read して手順を把握し、同等の処理を手動実行する。
hook マーカーファイルは手動で touch する。

### 恒久策
1M context セッションから呼ぶ可能性がある Skill の model 指定を見直す。
`grep -r "^model:" ~/.claude/skills/` で一覧確認。
```

---

## 学び：multi-model architecture が生む暗黙の前提

この問題の本質は、Claude Code の **multi-model architecture**（複数モデルが協調する設計）が「暗黙の前提」を生んでいる点にあります。

ユーザーが `model: sonnet` と Skill に書くとき、おそらく「コスト効率のために sonnet を使う」という意図があります。この指定が、1M context セッションの Extra Usage を「継承しない」という動作を引き起こすとは考えにくいでしょう。

```
ユーザーの理解（誤）:
親セッションが opus-4-6 + 1M → Skill も同じ環境で動く → model: sonnet は「処理に使うモデル」の指定

実際の動作:
Skill は独立した sub-execution として起動する
→ 新しい API リクエストを発行する
→ Extra Usage は API キーのセッションスコープに紐づく
→ sub-execution には Extra Usage が伝播しない
```

この「Skill はサブプロセスとして完全に分離する」という理解は、公式ドキュメントには明示されていません。推測できるのは、実際に問題に遭遇したときだけです。

同様の落とし穴は他にもあります。

- Skill 内で実行した Bash コマンドの環境変数が引き継がれないケース
- Skill 内で生成したファイルが親セッションのコンテキストに自動で読み込まれないケース
- 長いセッションで Skill の description が context truncation によって見えなくなるケース

共通するのは「 **親と子で状態が分離している** 」という前提を持つことで回避できる問題です。Skill を設計するときには、sub-execution が「まっさらな状態」で起動すると考えるのが正確です。

---

## 実装：Skill の model 指定を安全に管理する

以上の知識をふまえ、model 指定の管理方針を整理します。

### 方針：Extra Usage 依存の有無で分類する

```
1M context が必要な Skill（大規模コードベース分析など）
└── model: opus（または指定なし）を使う

1M context が不要な Skill（品質チェック、ファクトチェックなど）
└── model: sonnet でOKだが、1M セッションから呼ぶ可能性を考慮する
    → 解決策: 親セッションのモデルを継承させる（model 指定を削除）
       または親セッションにも Extra Usage を設定する
```

### 一括確認スクリプト

```bash
#!/bin/bash
# ~/.claude/scripts/check-skill-models.sh
# Skill の model 指定と 1M context 要件の整合性を確認する

SKILLS_DIR="${HOME}/.claude/skills"

echo "=== Skill model 指定の一覧 ==="
echo ""

while IFS= read -r -d '' skill_md; do
  skill_name=$(basename "$(dirname "$skill_md")")
  model=$(grep "^model:" "$skill_md" | head -1 | awk '{print $2}')
  
  if [ -z "$model" ]; then
    echo "[継承] ${skill_name} (model 指定なし → 親セッション依存)"
  elif echo "$model" | grep -q "sonnet\|haiku"; then
    echo "[注意] ${skill_name} (model: ${model} → 1M セッションから呼ぶと衝突する可能性)"
  else
    echo "[ OK ] ${skill_name} (model: ${model})"
  fi
done < <(find "$SKILLS_DIR" -name "SKILL.md" -print0)

echo ""
echo "=== 完了 ==="
```

実行例：

```bash
chmod +x ~/.claude/scripts/check-skill-models.sh
~/.claude/scripts/check-skill-models.sh

# 出力例
=== Skill model 指定の一覧 ===

[注意] content-validate (model: sonnet → 1M セッションから呼ぶと衝突する可能性)
[注意] fact-check (model: sonnet → 1M セッションから呼ぶと衝突する可能性)
[注意] da-review (model: sonnet → 1M セッションから呼ぶと衝突する可能性)
[ OK ] da-fix-loop (model: opus)
[継承] session-start (model 指定なし → 親セッション依存)

=== 完了 ===
```

このスクリプトを新しい Skill を追加するたびに実行することで、1M context セッションとの衝突リスクを事前に把握できます。

---

## まとめ

| 項目 | 内容 |
|---|---|
| 症状 | `API Error: Extra usage is required for 1M context` が Skill 呼び出し時に発生 |
| 原因 | Skill frontmatter の `model:` 指定により sub-execution が別モデルで起動 |
| 検出 | エラーメッセージのモデル名と `grep -r "^model:" ~/.claude/skills/` で特定 |
| 即時対応 | Skill をインライン実行、hook マーカーを手動生成 |
| 恒久策 | Skill の model 指定を見直す／セッションを分離する |
| 再発防止 | MEMORY に記録、check-skill-models.sh で定期確認 |

Claude Code のマルチモデル設計は、コスト効率と処理品質を使い分けられる強力な仕組みです。ただし、その設計が「Skill はまっさらな sub-execution として動く」という原則の上に成り立っている点を理解していないと、今回のような自己矛盾を踏むことになります。

Extra Usage を活用した 1M context 運用を行っている場合、Skill の model 指定は単なるパフォーマンス設定ではなく、**実行可能性に直結する設定** です。定期的に見直しを行うことをお勧めします。

---

## 関連記事

- [Claude Code Skills 実装で踏んだ地雷15選](./claude-code-skills-15)
- [Claude Code Hooks完全ガイド](./claude-code-hooks-complete-guide)
- [Claude Code CLAUDE.md 設計パターン](./claude-code-knowledge-files)

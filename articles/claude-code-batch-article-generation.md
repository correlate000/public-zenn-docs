---
title: "Claude Codeサブエージェントで100本記事を一晩生成した——バッチ並列処理と品質管理の全設計"
emoji: "📦"
type: "tech"
topics: ["claudecode", "ai", "automation", "contentpipeline"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「記事を量産したい」という欲望は、AIが登場してからずっと心の片隅にありました。しかしLLMに「記事を書いて」と投げるだけでは品質が安定しない。かといって1本ずつ手作業でレビューしていては量が出ない。

その答えとして行き着いたのが、Claude Codeのサブエージェント機能を使ったバッチ並列処理パイプラインです。結果として、11バッチ×5エージェント並列で計100本のZenn記事を一晩で生成しきりました。

本記事では、その設計・実装・失敗から得た知見をすべて公開します。「やってみた」で終わらせず、品質をどう担保したか、コンテキスト枯渇をどう回避したか、GitHub Actionsでの自動公開まで一気通貫で解説する。

:::message
この記事で紹介するコードとYAMLはそのまま使えます。ただし、LLMを使った大量生成はコスト管理が重要です。事前に `/cost-estimate` を実行して概算コストを確認してからの実行を推奨。
:::

## 全体アーキテクチャ

パイプライン全体は以下の5層で構成しています。

```
[スラグリスト管理] → [バッチ分割] → [並列エージェント実行]
    ↓
[front matterバリデーション] → [DAセルフレビュー]
    ↓
[GitHub Actions 毎日10本ずつ自動公開]
```

それぞれの層を順番に説明します。

## 1. スラグリスト設計——バッチの基盤

最初に「何を書くか」のリストを作るところから始まります。ここでの設計がバッチ全体の安定性を左右します。

### slugs.yaml の構造

```yaml
batches:
  - id: batch_001
    status: pending  # pending / running / done / error
    slugs:
      - claude-code-mcp-setup
      - claude-code-subagent-tips
      - claude-code-context-management
      - claude-code-hooks-pattern
      - claude-code-slash-commands
  - id: batch_002
    status: pending
    slugs:
      - nextjs-app-router-migration
      - nextjs-server-actions-pattern
      # ...
```

ポイントは `status` フィールドで冪等性を保つことです。エラーが起きたバッチだけ `status: error` になるので、再実行時は `pending` と `error` のみを対象にできます。全バッチを最初からやり直す必要はない。

### バッチサイズの決め方

1バッチあたり5スラグが最適でした。理由は2つ。

- ** コンテキスト枯渇の境界 **: 1エージェントが保持するコンテキストが8000トークン前後に収まる
- ** エラー影響範囲の限定 **: 1バッチが失敗しても5本の損失で済む

10本以上にするとエージェントが後半の記事で前半の内容を引きずり始め、質が落ちます。

## 2. バッチ実行スクリプト

Python で書いたオーケストレーターです。実際に使っているコードをそのまま載せます。

```python
#!/usr/bin/env python3
"""batch_runner.py — サブエージェント並列バッチ実行"""

import yaml
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

ARTICLES_DIR = Path("articles")
SLUGS_FILE = Path("scripts/slugs.yaml")
MAX_WORKERS = 5  # 並列エージェント数


def load_slugs() -> list[dict]:
    with open(SLUGS_FILE) as f:
        data = yaml.safe_load(f)
    return [b for b in data["batches"] if b["status"] in ("pending", "error")]


def update_batch_status(batch_id: str, status: str) -> None:
    with open(SLUGS_FILE) as f:
        data = yaml.safe_load(f)
    for batch in data["batches"]:
        if batch["id"] == batch_id:
            batch["status"] = status
            break
    with open(SLUGS_FILE, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def run_batch(batch: dict) -> tuple[str, bool]:
    """1バッチ = 1サブエージェントに委任"""
    batch_id = batch["id"]
    slugs = batch["slugs"]

    update_batch_status(batch_id, "running")

    prompt = build_prompt(slugs)
    result = subprocess.run(
        ["claude", "--print", "--no-markdown", prompt],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        update_batch_status(batch_id, "error")
        return batch_id, False

    update_batch_status(batch_id, "done")
    return batch_id, True


def build_prompt(slugs: list[str]) -> str:
    """自己完結プロンプト——外部ファイル参照禁止"""
    template = """あなたはZenn技術記事を書くエージェントです。
以下のスラグリストに対して、それぞれ1本ずつ記事を生成してください。

【厳守ルール】
- 外部ファイルを読みに行かない（探索禁止）
- 1スラグ = 1ファイル。複数スラグを1ファイルにまとめない
- front matterは必ず以下の形式で出力する

【front matter形式】
---
title: "記事タイトル"
emoji: "絵文字1つ"
type: "tech"
topics: ["topic1", "topic2", "topic3"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

【対象スラグ一覧】
{slugs}

各スラグに対して、articles/{{slug}}.md というパスでファイルを作成してください。
記事本文は300〜600行、です・ます調で記述してください。"""

    return template.format(slugs="\n".join(f"- {s}" for s in slugs))


def main():
    batches = load_slugs()
    if not batches:
        print("処理対象バッチなし")
        sys.exit(0)

    print(f"対象バッチ数: {len(batches)} / 並列数: {MAX_WORKERS}")

    success_count = 0
    error_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(run_batch, b): b for b in batches}
        for future in as_completed(futures):
            batch_id, ok = future.result()
            if ok:
                success_count += 1
                print(f"  ✓ {batch_id}")
            else:
                error_count += 1
                print(f"  ✗ {batch_id} (error)")

    print(f"\n完了: 成功={success_count} / エラー={error_count}")


if __name__ == "__main__":
    main()
```

## 3. コンテキスト枯渇対策——最重要

この設計で最も苦労したのがコンテキスト管理です。最初のバージョンでは以下の失敗をしました。

### やってはいけないパターン

```python
# ❌ 悪い例: テンプレートを外部ファイルから読ませる
prompt = f"templates/article-template.md を参照して、以下のスラグの記事を書いてください: {slugs}"
```

このプロンプトを渡すと、エージェントはまずテンプレートファイルを読み、次にサンプル記事を複数読み、さらに既存記事との重複チェックを始めます。その結果、肝心の記事生成に入る前にコンテキストの40%を消費してしまいます。

5本目の記事を書く頃には枯渇寸前で、品質が崩壊します。

### 正しいアプローチ

```python
# ✅ 良い例: テンプレートをプロンプトに埋め込む
template_content = Path("templates/article-template.md").read_text()
prompt = f"""以下のテンプレートに従って記事を書いてください。
外部ファイルは読まないこと。

=== テンプレート ===
{template_content}
===================

対象スラグ: {slugs}"""
```

プロンプト自体に必要な情報を全て含める「自己完結プロンプト」が原則です。エージェントが探索する理由をなくすことで、コンテキストを記事生成だけに集中させられます。

### 1エージェント = 1記事の原則

5本を1エージェントに書かせる設計にしましたが、実装上は各記事を独立したTask（Tool Use）として扱うようにプロンプト設計しています。

```
エージェント起動
  └─ Task 1: articles/slug-a.md を生成
  └─ Task 2: articles/slug-b.md を生成
  └─ Task 3: articles/slug-c.md を生成
  └─ Task 4: articles/slug-d.md を生成
  └─ Task 5: articles/slug-e.md を生成
```

各Taskが終わるたびにファイルをディスクに書き出すので、途中でクラッシュしても生成済み分は失われません。

## 4. front matterバリデーター

100本生成すると、front matterのミスが必ず発生します。よくある問題は：

- `topics` がYAMLリスト形式になっている（インライン配列が必要）
- `published` が文字列の `"false"` になっている（boolean が必要）
- `emoji` が空だったり2文字になっている

バリデーターで一括チェックします。

```python
#!/usr/bin/env python3
"""validate_frontmatter.py — front matter一括検証"""

import re
import sys
from pathlib import Path

ARTICLES_DIR = Path("articles")

REQUIRED_KEYS = ["title", "emoji", "type", "topics", "published", "status", "publication_name"]


def validate_file(path: Path) -> list[str]:
    errors = []
    content = path.read_text()

    # front matter抽出
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return [f"{path.name}: front matterが見つかりません"]

    fm_text = match.group(1)

    # 必須キー確認
    for key in REQUIRED_KEYS:
        if f"{key}:" not in fm_text:
            errors.append(f"{path.name}: '{key}' がありません")

    # topics形式確認（インライン配列）
    topics_match = re.search(r"topics:\s*(.*)", fm_text)
    if topics_match:
        topics_val = topics_match.group(1).strip()
        if topics_val.startswith("-"):
            errors.append(f"{path.name}: topics がYAMLリスト形式。インライン配列 ['a','b'] に修正してください")
        if not topics_val.startswith("["):
            errors.append(f"{path.name}: topics の形式が不正: {topics_val}")

    # emoji確認（1文字）
    emoji_match = re.search(r"emoji:\s*\"(.+?)\"", fm_text)
    if emoji_match:
        emoji_val = emoji_match.group(1)
        if len(emoji_val) > 2:  # 絵文字は内部的に2バイト以上になることがある
            errors.append(f"{path.name}: emoji が長すぎます: {emoji_val}")

    # publication_name確認
    if 'publication_name: "correlate_dev"' not in fm_text:
        errors.append(f"{path.name}: publication_name が correlate_dev ではありません")

    return errors


def main():
    paths = sorted(ARTICLES_DIR.glob("*.md"))
    total_errors = []

    for path in paths:
        errors = validate_file(path)
        total_errors.extend(errors)

    if total_errors:
        print(f"❌ エラー {len(total_errors)} 件:")
        for e in total_errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"✅ {len(paths)} 件、すべて正常")


if __name__ == "__main__":
    main()
```

100本を検証した際のエラー件数は12件。ほぼ全て `topics` のフォーマットミスでした。

## 5. DAセルフレビューの統合

生成直後の記事をそのまま公開するのはリスクがあります。ここでDAレビュー（Devil's Advocate Review）を挟みます。

ただし、100本全てに対してフルDAレビューを走らせるとコストと時間が膨大になります。そこで「品質スコアリング」を先行させ、スコアが低い記事だけ詳細レビューにかける2段階方式を採用しています。

```python
def quick_quality_score(path: Path) -> float:
    """簡易品質スコア（0.0〜1.0）"""
    content = path.read_text()
    score = 1.0

    # コードブロックがない記事は品質が低い傾向
    if "```" not in content:
        score -= 0.3

    # 文字数が少なすぎる
    if len(content) < 3000:
        score -= 0.3

    # 見出しが2つ以下
    h2_count = content.count("\n## ")
    if h2_count < 3:
        score -= 0.2

    # :::message ブロックがない（Zenn固有のリッチ表現を使えているか）
    if ":::message" not in content:
        score -= 0.1

    return max(0.0, score)
```

スコアが0.6未満の記事（今回は7本）はDAレビューにかけ、指摘を受けて再生成します。

## 6. GitHub Actions による段階的公開

100本を一気に公開すると、Zennのレートリミットに引っかかる可能性があります。また読者への届け方としても、毎日少量ずつの方が継続的な露出になります。

`daily-publish.py` と GitHub Actions を組み合わせて、1日10本ずつ自動公開します。

```python
#!/usr/bin/env python3
"""daily-publish.py — 毎日10本を published: true に変更"""

import re
import yaml
from pathlib import Path

ARTICLES_DIR = Path("articles")
DAILY_LIMIT = 10


def get_draft_articles() -> list[Path]:
    drafts = []
    for path in sorted(ARTICLES_DIR.glob("*.md")):
        content = path.read_text()
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            continue
        fm = yaml.safe_load(match.group(1))
        if not fm.get("published", True):
            drafts.append(path)
    return drafts


def publish_article(path: Path) -> None:
    content = path.read_text()
    # published: false → published: true
    updated = re.sub(
        r"^published: false$",
        "published: true",
        content,
        flags=re.MULTILINE,
    )
    # status: "draft" → status: "published"
    updated = re.sub(
        r'^status: "draft"$',
        'status: "published"',
        updated,
        flags=re.MULTILINE,
    )
    path.write_text(updated)
    print(f"  公開: {path.name}")


def main():
    drafts = get_draft_articles()
    targets = drafts[:DAILY_LIMIT]

    if not targets:
        print("公開待ち記事なし")
        return

    for path in targets:
        publish_article(path)

    print(f"\n本日公開: {len(targets)}本 / 残り: {len(drafts) - len(targets)}本")


if __name__ == "__main__":
    main()
```

これを毎日9:00 JSTに実行するワークフローです。

```yaml
# .github/workflows/daily-publish.yml
name: Daily Article Publish

on:
  schedule:
    - cron: "0 0 * * *"  # UTC 0:00 = JST 9:00
  workflow_dispatch:

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install pyyaml

      - name: Run daily publish
        run: python scripts/daily-publish.py

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add articles/
          git diff --staged --quiet || git commit -m "chore: daily publish $(date +%Y-%m-%d)"
          git push
```

`git diff --staged --quiet ||` の部分が重要です。公開対象がない日でも、ワークフローがエラーで落ちないようにしています。

## 7. 実際の数値

100本生成を完了した際の実測値を共有します。

| 指標 | 値 |
|------|----|
| 総記事数 | 100本 |
| バッチ数 | 11バッチ（最後のバッチは5本未満） |
| 並列エージェント数 | 5 |
| 総所要時間 | 約4時間（一晩で完了） |
| front matterエラー件数 | 12件 |
| 品質スコア0.6未満（要再生成） | 7本 |
| 最終的にDAレビュー通過した本数 | 100本 |
| GitHub Actions公開完了までの日数 | 10日間（10本/日） |

コストは非公開ですが、claude-opus-4系を使わずsonnetで固定したことでかなり抑えられました。品質とコストのバランスを考えると、記事生成はsonnetで十分です。

## ハマりポイントまとめ

実装中に直面した問題を整理します。

### 問題1: エージェントが既存記事を読み始める

エージェントに「重複しないよう注意して」と指示すると、既存の全記事を読み始めます。100本あると読むだけでコンテキストを食い尽くします。

** 解決策 **: 重複チェックの指示を完全に削除。スラグリストで重複を管理する責任をオーケストレーター側に持たせました。

### 問題2: 途中でエージェントが停止する

長時間実行中に接続タイムアウトや予期しない停止が発生します。

** 解決策 **: `slugs.yaml` の `status` フィールドで冪等性を確保。停止したバッチだけ `status: error` にして再実行できるようにしました。

### 問題3: 後半の記事品質が落ちる

1バッチで10本以上書かせると、後半になるほど内容が薄くなります。

** 解決策 **: バッチサイズを5本に固定。1エージェントあたりの負荷を抑えることで品質を安定させました。

### 問題4: topics に日本語が入る

生成されたtopicsに日本語が混入することがありました（例: `["claude", "ai", "自動化"]`）。

** 解決策 **: バリデーターに日本語チェックを追加。

```python
# バリデーターに追加
topics_match = re.search(r"topics:\s*\[(.+?)\]", fm_text)
if topics_match:
    for topic in topics_match.group(1).split(","):
        topic = topic.strip().strip('"').strip("'")
        if re.search(r"[^\x00-\x7F]", topic):
            errors.append(f"{path.name}: topics に日本語が含まれています: {topic}")
```

## まとめ

Claude Codeのサブエージェント機能でバッチ並列処理を構築し、100本記事を一晩で生成するパイプラインを完成させました。

重要な設計原則を再整理します。

1. ** 自己完結プロンプト **: 外部ファイルへの参照を排除し、必要な情報はプロンプトに埋め込む
2. ** バッチサイズは5本以下 **: コンテキスト枯渇の境界を超えない
3. ** 冪等性の確保 **: statusフィールドで再実行を安全に行えるようにする
4. ** バリデーターは必須 **: front matterのミスは必ず発生する前提で作る
5. ** 段階的公開 **: 一気に公開せず、GitHub Actionsで毎日少量ずつ出す

量産と品質は対立しません。設計できちんと分離すれば両立できます。本記事のコードをベースに、自分のコンテンツパイプラインを構築してみてください。

---

関連記事:
- [Claude Code Agent Teamsで開発タスクを並列処理した実践ガイド](https://zenn.dev/correlate_dev/articles/agent-teams-parallel)
- [デビルズアドボケイトをAI開発チームに入れたら品質が劇的に改善した話](https://zenn.dev/correlate_dev/articles/devils-advocate-ai-team)

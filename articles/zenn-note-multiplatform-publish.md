---
title: "ZennとNoteに同時投稿する自動化パイプラインをGitHub Actionsで構築する"
emoji: "🔁"
type: "tech"
topics: ["githubactions", "zenn", "markdown", "automation", "python"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

技術ブログを書いていると「Zennにはエンジニア読者がいる、でもnoteのフォロワーにも届けたい」という状況に直面します。筆者も以前は毎回手動でコピペしていたのですが、フォーマット変換ミス・更新漏れ・メタデータの不整合が積み重なり、運用が破綻しかけました。

本記事では、**1つのMarkdownファイルを Single Source of Truth として、ZennとNoteに自動で配信するパイプライン**の構築方法を解説します。GitHub Actions・Python スクリプト・Playwright を組み合わせた実装例を中心に、コピーして使えるコードを提供します。

---

## なぜマルチプラットフォーム配信が必要か

### ZennとNoteのオーディエンス特性の違い

| 観点 | Zenn | note |
|------|------|------|
| 主要ユーザー層 | ソフトウェアエンジニア・技術者 | クリエイター全般・ビジネス層 |
| 記事形式 | Markdown・GitHub連携 | リッチテキストエディタ |
| マネタイズ | 有料Book・スクラップ | 有料記事・メンバーシップ |
| SEOの強さ | 技術系キーワードで強い | 一般検索で広いリーチ |
| APIサポート | GitHub連携で実質API | 非公式のみ |

Zennは技術記事との親和性が高く、エンジニアコミュニティへのリーチに優れています。一方、noteはクリエイター・ビジネス層に届きやすく、マネタイズ機能も充実しています。どちらか一方を選ぶより、**両方に届ける**方が情報発信の費用対効果は高くなります。

### 手動コピペ運用の問題点

手動で二重管理すると以下の問題が発生します。

- **ミス**: Zenn独自記法（`:::message`など）がnoteに残ったまま公開される
- **時間コスト**: フォーマット変換・タグ設定・画像の再アップロードで30〜60分/記事
- **更新漏れ**: Zennで誤字修正してもnoteは古いまま
- **メタデータ不整合**: タイトルや公開日がプラットフォームごとに異なる

### 本記事で構築するアーキテクチャ

```
[ローカル執筆]
    ↓ git push
[GitHub Repository]
    ├── Zenn GitHub連携 → Zenn自動デプロイ
    └── GitHub Actions
            ├── textlint（校正）
            ├── 変換スクリプト（Markdown → note互換）
            └── Playwright（note自動投稿）
```

---

## プラットフォーム仕様の差異を把握する

### Zennの技術仕様

Zennは**GitHubリポジトリと連携**することで、pushするだけで記事が公開・更新されます。

リポジトリ構造は以下が必要です。

```
my-zenn-content/
├── articles/
│   └── my-article.md    # slug がファイル名になる
├── books/
└── .gitignore
```

frontmatterの必須フィールドは以下のとおりです。

```yaml
---
title: "記事タイトル"
emoji: "🔥"
type: "tech"        # tech or idea
topics: ["python", "github"]
published: true
---
```

また、Zenn独自のMarkdown記法として以下があります。

```markdown
:::message
情報メッセージ（青いボックス）
:::

:::message alert
警告メッセージ（赤いボックス）
:::

:::details 折りたたみタイトル
折りたたみコンテンツ
:::

@[tweet](https://twitter.com/...)
@[youtube](動画ID)
```

これらはnoteでは**レンダリングされない**ため、変換が必要です。

### noteの技術仕様

noteには**公式APIが存在しない**（2025年時点）ため、自動化はブラウザ操作（Playwright）を使うことになります。

noteエディタの特性として、以下を把握しておく必要があります。

- Markdownではなくリッチテキストエディタが基本
- コードブロックはサポートしているが、シンタックスハイライトは限定的
- 画像は外部URLではなく**noteのCDNへのアップロードが原則**
- 埋め込み（YouTube等）はURLを貼ると自動展開

### 互換・非互換マッピング表

| 要素 | Zenn | note | 変換方法 |
|------|------|------|----------|
| 見出し（H1〜H4） | ✅ | ✅（H4以下は非推奨） | そのまま |
| コードブロック | ✅ | ✅ | そのまま |
| 画像 | ✅ | ✅（CDN要） | URL変換 |
| テーブル | ✅ | ✅ | そのまま |
| `:::message` | ✅ | ❌ | blockquoteに変換 |
| `:::details` | ✅ | ❌ | 展開またはHTMLに変換 |
| 数式（KaTeX） | ✅ | ❌ | 画像化または省略 |
| 脚注 | ✅ | ❌ | インラインテキストに変換 |
| @[tweet] | ✅ | ❌ | URLリンクに変換 |

---

## ローカル執筆環境の構築

### リポジトリ構造の設計

Single Source of Truth を実現するディレクトリ設計です。

```
my-content/
├── articles/
│   └── 2025-01-15-my-article.md   # 原稿ファイル
├── images/
│   └── 2025-01-15-my-article/
│       └── hero.png
├── scripts/
│   ├── convert_to_note.py          # 変換スクリプト
│   ├── upload_images.py            # Cloudinary連携
│   └── post_to_note.py             # note自動投稿
├── .github/
│   └── workflows/
│       └── publish.yml             # GitHub Actions
├── .textlintrc                     # 校正ルール
└── .gitignore
```

### Zenn CLIのセットアップ

```bash
# Node.js 18+ が前提
npm install zenn-cli

# 初期化（articles/, books/ ディレクトリが生成される）
npx zenn init

# ローカルプレビュー（http://localhost:8000）
npx zenn preview
```

### frontmatterによる一元メタデータ管理

カスタムフィールドを使って、プラットフォーム別の設定を1ファイルで管理します。

```yaml
---
# === Zenn 標準フィールド ===
title: "記事タイトル"
emoji: "🔧"
type: "tech"
topics: ["python", "automation", "github"]
published: false

# === 配信設定（カスタムフィールド）===
destinations: ["zenn", "note"]
canonical_url: "https://zenn.dev/yourname/articles/my-article"

# === note 用設定 ===
note_tags: ["プログラミング", "自動化", "Python"]
note_hashtags: ["エンジニア"]

# === 管理用 ===
created_at: "2025-01-15"
updated_at: "2025-01-15"
zenn_published_at: ""
note_published_at: ""
---
```

`destinations` フィールドで配信先を制御し、noteへの投稿が不要な記事はワークフローをスキップできます。

---

## Markdown変換スクリプトの実装

### 変換パイプラインの設計

AST（抽象構文木）ベースの変換と正規表現変換の使い分けは以下のとおりです。

| 変換対象 | 推奨手法 | 理由 |
|----------|----------|------|
| Zenn独自記法 | 正規表現 | シンプルな記法変換で十分 |
| 見出し・コードブロック | AST（remark） | 入れ子構造を正確に処理 |
| 画像パス | 正規表現 + API | URLの差し替えが主目的 |
| frontmatter | gray-matter | 専用パーサーで安全に処理 |

本記事ではPythonで実装します。Node.jsのremarkエコシステムを使いたい場合は後述のNode.js版も参照してください。

### Zenn独自記法のnote互換変換

`scripts/convert_to_note.py` の実装です。

```python
import re
import sys
from pathlib import Path

import yaml  # PyYAML


def load_frontmatter(content: str) -> tuple[dict, str]:
    """frontmatterとbodyを分離して返す"""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    meta = yaml.safe_load(parts[1])
    body = parts[2].lstrip("\n")
    return meta, body


def convert_message_blocks(text: str) -> str:
    """:::message → blockquote 変換"""
    # :::message alert
    text = re.sub(
        r":::message alert\n(.*?):::",
        lambda m: f"> ⚠️ **注意**\n>\n> {m.group(1).strip()}",
        text,
        flags=re.DOTALL,
    )
    # :::message（通常）
    text = re.sub(
        r":::message\n(.*?):::",
        lambda m: f"> 💡 **ポイント**\n>\n> {m.group(1).strip()}",
        text,
        flags=re.DOTALL,
    )
    return text


def convert_details_blocks(text: str) -> str:
    """:::details → 展開テキスト変換"""
    def replace_details(m: re.Match) -> str:
        title = m.group(1).strip()
        body = m.group(2).strip()
        return f"**{title}**\n\n{body}"

    return re.sub(
        r":::details\s+(.+?)\n(.*?):::",
        replace_details,
        text,
        flags=re.DOTALL,
    )


def convert_embed_links(text: str) -> str:
    """@[tweet] / @[youtube] → プレーンURLリンク変換"""
    # Twitter/X埋め込み
    text = re.sub(
        r"@\[tweet\]\((https://twitter\.com/[^\)]+)\)",
        r"[\1](\1)",
        text,
    )
    # YouTube埋め込み
    text = re.sub(
        r"@\[youtube\]\(([a-zA-Z0-9_-]+)\)",
        lambda m: f"[YouTube動画](https://www.youtube.com/watch?v={m.group(1)})",
        text,
    )
    return text


def convert_footnotes(text: str) -> str:
    """脚注をインラインテキストに変換"""
    # 脚注定義の収集
    footnote_defs: dict[str, str] = {}
    def collect_def(m: re.Match) -> str:
        footnote_defs[m.group(1)] = m.group(2)
        return ""  # 定義行を削除

    text = re.sub(r"^\[\^(\w+)\]:\s*(.+)$", collect_def, text, flags=re.MULTILINE)

    # 脚注参照をインライン展開
    def expand_ref(m: re.Match) -> str:
        key = m.group(1)
        note = footnote_defs.get(key, "")
        return f"（{note}）" if note else ""

    text = re.sub(r"\[\^(\w+)\]", expand_ref, text)
    return text


def convert_for_note(source_path: str) -> str:
    """メイン変換関数"""
    content = Path(source_path).read_text(encoding="utf-8")
    meta, body = load_frontmatter(content)

    # 配信先チェック
    destinations = meta.get("destinations", [])
    if "note" not in destinations:
        print(f"[SKIP] {source_path}: note は配信対象外です")
        return ""

    # 変換パイプライン
    body = convert_message_blocks(body)
    body = convert_details_blocks(body)
    body = convert_embed_links(body)
    body = convert_footnotes(body)

    # noteのタグ情報をフッターに追加
    note_tags = meta.get("note_tags", [])
    if note_tags:
        tags_line = " ".join(f"#{tag}" for tag in note_tags)
        body += f"\n\n---\n{tags_line}"

    return body


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_to_note.py <source.md>")
        sys.exit(1)

    result = convert_for_note(sys.argv[1])
    if result:
        output_path = sys.argv[1].replace(".md", "_note.md")
        Path(output_path).write_text(result, encoding="utf-8")
        print(f"[OK] 変換完了: {output_path}")
```

### 画像パスの解決とCloudinaryアップロード

ローカル画像をnote投稿前にCloudinaryへアップロードし、絶対URLに変換します。

```python
import re
import os
from pathlib import Path

import cloudinary
import cloudinary.uploader


def setup_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
    )


def upload_and_replace_images(content: str, article_dir: Path) -> str:
    """ローカル画像パスをCloudinary URLに置換する"""
    setup_cloudinary()

    def replace_image(m: re.Match) -> str:
        alt = m.group(1)
        path = m.group(2)

        # 外部URLはスキップ
        if path.startswith("http"):
            return m.group(0)

        local_path = article_dir / path
        if not local_path.exists():
            print(f"[WARN] 画像が見つかりません: {local_path}")
            return m.group(0)

        # Cloudinaryにアップロード
        result = cloudinary.uploader.upload(
            str(local_path),
            folder="zenn-articles",
            overwrite=False,
        )
        cdn_url = result["secure_url"]
        print(f"[UPLOAD] {local_path.name} → {cdn_url}")
        return f"![{alt}]({cdn_url})"

    return re.sub(r"!\[([^\]]*)\]\(([^\)]+)\)", replace_image, content)
```

⚠️ **ハマりポイント**: `overwrite=False` を指定しないと同名ファイルを再アップロードするたびにCloudinaryのストレージを消費します。public_idで管理する方法も検討してください。

---

## GitHub Actionsパイプラインの実装

### ワークフロー全体設計

`.github/workflows/publish.yml` です。

```yaml
name: Publish Articles

on:
  push:
    branches: [main]
    paths:
      - "articles/**"
  workflow_dispatch:
    inputs:
      article_path:
        description: "対象記事のパス（例: articles/my-article.md）"
        required: false

jobs:
  # ── ジョブ1: Lint ──────────────────────────────────
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"

      - run: npm ci

      - name: textlint
        run: npx textlint "articles/**/*.md"

  # ── ジョブ2: note配信 ──────────────────────────────
  post-to-note:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2  # 変更ファイル検出のため

      - name: 変更記事を検出
        id: detect
        run: |
          CHANGED=$(git diff --name-only HEAD~1 HEAD -- "articles/*.md" | head -1)
          echo "article=${CHANGED}" >> "$GITHUB_OUTPUT"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - run: pip install -r scripts/requirements.txt

      - name: Markdown変換
        if: steps.detect.outputs.article != ''
        run: |
          python scripts/convert_to_note.py "${{ steps.detect.outputs.article }}"
        env:
          CLOUDINARY_CLOUD_NAME: ${{ secrets.CLOUDINARY_CLOUD_NAME }}
          CLOUDINARY_API_KEY: ${{ secrets.CLOUDINARY_API_KEY }}
          CLOUDINARY_API_SECRET: ${{ secrets.CLOUDINARY_API_SECRET }}

      - name: Playwright セットアップ
        run: |
          pip install playwright
          playwright install chromium --with-deps

      - name: noteに投稿
        if: steps.detect.outputs.article != ''
        run: |
          python scripts/post_to_note.py "${{ steps.detect.outputs.article }}"
        env:
          NOTE_EMAIL: ${{ secrets.NOTE_EMAIL }}
          NOTE_PASSWORD: ${{ secrets.NOTE_PASSWORD }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

### note自動投稿スクリプト（Playwright実装）

`scripts/post_to_note.py` です。

```python
import os
import sys
import time
import json
import re
from pathlib import Path

import requests
import yaml
from playwright.sync_api import sync_playwright, Page


NOTE_URL = "https://note.com"


def load_article(path: str) -> tuple[dict, str]:
    content = Path(path).read_text(encoding="utf-8")
    if content.startswith("---"):
        parts = content.split("---", 2)
        meta = yaml.safe_load(parts[1])
        body = parts[2].lstrip("\n")
    else:
        meta, body = {}, content

    # 変換済みファイルを読む
    note_path = path.replace(".md", "_note.md")
    if Path(note_path).exists():
        body = Path(note_path).read_text(encoding="utf-8")

    return meta, body


def login(page: Page) -> None:
    """noteにログイン"""
    page.goto(f"{NOTE_URL}/login")
    page.wait_for_load_state("networkidle")

    page.fill('input[name="email"]', os.environ["NOTE_EMAIL"])
    page.fill('input[name="password"]', os.environ["NOTE_PASSWORD"])
    page.click('button[type="submit"]')
    page.wait_for_url(f"{NOTE_URL}/**", timeout=15_000)
    print("[OK] ログイン完了")


def create_draft(page: Page, meta: dict, body: str) -> str:
    """note記事を下書き作成してURLを返す"""
    page.goto(f"{NOTE_URL}/notes/new")
    page.wait_for_load_state("networkidle")
    time.sleep(2)  # エディタの初期化待ち

    # タイトル入力
    title_input = page.locator('[data-testid="noteTitle"]')
    title_input.click()
    title_input.fill(meta.get("title", ""))

    # 本文入力（Markdownをクリップボード経由でペースト）
    body_area = page.locator('[contenteditable="true"]').nth(1)
    body_area.click()
    page.evaluate(f"""
        const text = {json.dumps(body)};
        navigator.clipboard.writeText(text);
    """)
    page.keyboard.press("Control+v")
    time.sleep(1)

    # 下書き保存
    page.click('[data-testid="saveDraftButton"]')
    page.wait_for_load_state("networkidle")

    current_url = page.url
    print(f"[OK] 下書き保存: {current_url}")
    return current_url


def notify_slack(webhook_url: str, message: str) -> None:
    if not webhook_url:
        return
    requests.post(webhook_url, json={"text": message}, timeout=10)


def post_to_note(article_path: str) -> None:
    meta, body = load_article(article_path)

    # 配信先チェック
    if "note" not in meta.get("destinations", []):
        print(f"[SKIP] note は配信対象外: {article_path}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            login(page)
            url = create_draft(page, meta, body)

            notify_slack(
                os.environ.get("SLACK_WEBHOOK_URL", ""),
                f"✅ note 下書き作成完了\n記事: {meta.get('title')}\nURL: {url}",
            )
        except Exception as e:
            notify_slack(
                os.environ.get("SLACK_WEBHOOK_URL", ""),
                f"❌ note 投稿失敗\n記事: {article_path}\nエラー: {e}",
            )
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python post_to_note.py <article.md>")
        sys.exit(1)
    post_to_note(sys.argv[1])
```

⚠️ **ハマりポイント**: noteのDOM構造は予告なく変更されます。`data-testid` 属性はある程度安定していますが、定期的な動作確認が必要です。本番投稿前に**必ず下書き保存で動作を確認**してから公開フローに移行してください。

⚠️ **ハマりポイント**: クリップボード経由のペーストはheadless環境では動作しないことがあります。その場合は `page.keyboard.type()` を使いますが、日本語の長文では時間がかかります。`xdotool` や `xclip` を使う方法もあります（Linux環境）。

---

## SEOリスク対策と品質管理

### 重複コンテンツ問題とcanonical URLの設定

同じ内容を複数サイトに公開すると、Googleの重複コンテンツとして評価が分散するリスクがあります。対策は `canonical URL` の設定です。

**Zennでの設定:**

```yaml
---
title: "記事タイトル"
# ...
canonical_url: "https://zenn.dev/yourname/articles/my-article"
---
```

Zennは `canonical_url` frontmatterフィールドを公式サポートしています。自分のZenn記事URLを指定することで、「ここが正規ページ」と検索エンジンに伝えられます。

**noteでの対応:**

noteはcanonical URLを設定できないため、代替として**記事末尾に「初出: Zenn」リンク**を追加します。変換スクリプトに以下を追加します。

```python
def add_canonical_footer(body: str, meta: dict) -> str:
    """noteフッターにcanonical情報を追記"""
    canonical = meta.get("canonical_url", "")
    if not canonical:
        return body

    footer = f"\n\n---\n📝 この記事は [Zenn]({canonical}) にも掲載しています。"
    return body + footer
```

### textlintによる校正自動化

`.textlintrc` の推奨設定です。

```json
{
  "rules": {
    "preset-ja-technical-writing": {
      "sentence-length": {
        "max": 100
      },
      "no-exclamation-question-mark": true,
      "no-doubled-joshi": true
    },
    "preset-ja-spacing": true,
    "no-todo": true
  },
  "plugins": {
    "@textlint/markdown": {
      "extensions": [".md"]
    }
  }
}
```

`package.json` への追加:

```json
{
  "devDependencies": {
    "textlint": "^13.0.0",
    "textlint-rule-preset-ja-technical-writing": "^9.0.0",
    "textlint-rule-preset-ja-spacing": "^2.4.2"
  },
  "scripts": {
    "lint": "textlint articles/**/*.md",
    "lint:fix": "textlint --fix articles/**/*.md"
  }
}
```

### 運用フローとモニタリング

記事修正時の再デプロイフローは以下です。

```
1. articles/my-article.md を修正してコミット
2. GitHub Actions が自動で起動
   ├── lint 通過
   ├── convert_to_note.py で変換
   └── post_to_note.py で下書き更新
3. Slack通知で確認
4. noteの下書きを手動で公開（または自動公開）
```

Zennは git push で自動更新されるため追加作業は不要です。

---

## 実際に動かしてみる

### 最小構成での動作確認

```bash
# 1. リポジトリのクローン
git clone https://github.com/yourname/zenn-content.git
cd zenn-content

# 2. 依存関係のインストール
npm install
pip install -r scripts/requirements.txt
playwright install chromium

# 3. Zennのローカルプレビュー
npx zenn preview
# → http://localhost:8000 で確認

# 4. 変換スクリプトのローカルテスト
python scripts/convert_to_note.py articles/my-article.md
# → articles/my-article_note.md が生成される

# 5. 変換結果の確認
cat articles/my-article_note.md
```

### よくあるエラーとトラブルシューティング

**Q: Playwright が note のDOM要素を見つけられない**

```
Error: Locator '[data-testid="noteTitle"]' not found
```

noteのUI更新が原因です。以下で現在のDOM構造を確認します。

```python
# デバッグ用：headless=False でブラウザを表示
browser = p.chromium.launch(headless=False, slow_mo=500)
page.screenshot(path="debug.png")  # スクリーンショットで確認
```

**Q: 画像アップロードが失敗する**

```
cloudinary.exceptions.Error: Invalid credentials
```

GitHub Secrets の設定を確認します。環境変数名のタイポが原因であることがほとんどです。

```bash
# ローカルで確認
export CLOUDINARY_CLOUD_NAME="your-name"
python scripts/upload_images.py articles/images/test.png
```

**Q: GitHub Actions で `playwright install` が遅い**

キャッシュを設定します。

```yaml
- name: Playwright キャッシュ
  uses: actions/cache@v4
  with:
    path: ~/.cache/ms-playwright
    key: playwright-${{ hashFiles('**/requirements.txt') }}

- run: playwright install chromium --with-deps
```

### 発展的な応用例

**Hashnodeへの展開:**

HashnodeはGraphQL APIを公式提供しているため、Playwrightは不要です。

```python
import httpx

def post_to_hashnode(meta: dict, body: str) -> None:
    query = """
    mutation CreatePublicationStory($input: CreateStoryInput!) {
      createPublicationStory(input: $input, publicationId: "YOUR_PUBLICATION_ID") {
        post { slug }
      }
    }
    """
    variables = {
        "input": {
            "title": meta["title"],
            "contentMarkdown": body,
            "tags": [],
        }
    }
    resp = httpx.post(
        "https://api.hashnode.com",
        json={"query": query, "variables": variables},
        headers={"Authorization": os.environ["HASHNODE_API_KEY"]},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"[OK] Hashnode投稿完了: {resp.json()}")
```

**NotionをヘッドレスCMSとして使う構成:**

NotionのページをMarkdownにエクスポートし、piplineに流し込む構成も有効です。`notion-to-md` ライブラリを活用することで、Notionのデータベースを原稿管理の起点にできます。

---

## まとめ

本記事では、ZennとNoteへのマルチプラットフォーム配信パイプラインを構築しました。

| 実装項目 | ツール |
|----------|--------|
| 原稿管理 | GitHub + Zenn CLI |
| メタデータ一元管理 | frontmatter カスタムフィールド |
| Markdown変換 | Python（正規表現ベース） |
| 画像CDN | Cloudinary |
| CI/CD | GitHub Actions |
| note自動投稿 | Playwright |
| 校正 | textlint |
| SEO対策 | canonical URL + フッター注記 |

最大のポイントは**Single Source of Truth の徹底**です。原稿ファイルが唯一の正であり、プラットフォームごとの差異は変換スクリプトが吸収するという設計により、手動作業を最小化できます。

noteのブラウザ自動化は脆弱な部分もありますが、下書き保存止まりにしておき、最終的な公開は目視確認後に手動で行うという運用が現実的で安全です。

このパイプラインをベースに、Qiita・Hashnode・個人ブログなど配信先を増やしていくことも難しくありません。まずは変換スクリプト単体から試してみることをお勧めします。

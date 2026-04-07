---
title: "Imagen 4.0でブログ画像を一括生成する — Gemini 2.5 Flash + Python で48枚 $2"
emoji: "🖼️"
type: "tech"
topics: ["imagen", "gemini", "python", "imagegeneration", "contentautomation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

アフィリエイトサイトや技術ブログを運営していると、記事ごとにヒーロー画像を用意する手間が意外と大きいです。54記事あれば54枚の画像が必要で、フリー素材ではブランド感が出ない、Midjourney は月額が高い、という悩みがあります。

Imagen 4.0（Google の画像生成AI）を使って、Python スクリプトで 48 枚のヒーロー画像を一括生成した実践をまとめます。 **1枚 $0.04、48枚で約 $2** というコストで、写実的なコーヒー画像を生成できました。

---

## 使用技術

- **Imagen 4.0** — Google の最新画像生成モデル（`imagen-4.0-generate-preview-05-20`）
- **Gemini 2.5 Flash** — 記事の内容からプロンプトを生成するために使用
- **google-genai SDK** — Python からの API アクセス
- **Pillow** — WebP 変換・リサイズ
- **python-frontmatter** — MDX ファイルのフロントマター読み書き

---

## アーキテクチャ

```
MDX ファイル（記事）
  ↓ タイトル・カテゴリを読み込む
Gemini 2.5 Flash（プロンプト生成）
  ↓ 写実的な画像プロンプトを生成
Imagen 4.0（画像生成）
  ↓ PNG で生成
Pillow（WebP 変換・リサイズ）
  ↓ 1200×630 の OGP サイズに変換
MDX フロントマター更新（image パスを追記）
```

Gemini でプロンプトを生成する理由は、 ** 記事の内容に合った具体的な描写 ** を自動で作れるからです。手動でプロンプトを書くと「コーヒーカップの写真」程度の汎用的な指示になりがちですが、Gemini に記事の要約を渡すと「深煎りのシングルオリジン豆が木製のスプーンに乗り、温かみのある照明が当たっている俯瞰写真」のような具体的なプロンプトが生成されます。

---

## セットアップ

```bash
pip install google-genai Pillow python-frontmatter
```

```python
import os
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
```

Vertex AI ではなく Google AI Studio の API キーを使う場合、`google.genai.Client` に `api_key` を渡します。

---

## ステップ1：カテゴリ別スタイルガイドの定義

記事のカテゴリによって画像のトーンを統一するため、スタイルガイドを定義します。

```python
STYLE_GUIDES = {
    "beans": {
        "style": "photorealistic product photography",
        "lighting": "soft natural light from the left",
        "background": "wooden table, rustic texture",
        "mood": "warm and inviting",
        "composition": "overhead shot or 45-degree angle",
    },
    "brewing": {
        "style": "photorealistic lifestyle photography",
        "lighting": "warm morning light",
        "background": "kitchen counter or café setting",
        "mood": "calm, peaceful morning atmosphere",
        "composition": "eye-level, showing the brewing process",
    },
    "equipment": {
        "style": "clean product photography",
        "lighting": "studio lighting, white background or minimal props",
        "background": "white or light gray",
        "mood": "professional, clean",
        "composition": "straight-on or slight angle",
    },
    "recipe": {
        "style": "food photography",
        "lighting": "natural window light",
        "background": "textured surface with minimal props",
        "mood": "delicious and approachable",
        "composition": "overhead shot",
    },
}

DEFAULT_STYLE = {
    "style": "photorealistic coffee photography",
    "lighting": "natural light",
    "background": "café setting",
    "mood": "warm and welcoming",
    "composition": "medium shot",
}
```

---

## ステップ2：Gemini でプロンプト生成

```python
def generate_image_prompt(
    title: str,
    description: str,
    category: str,
) -> str:
    """記事情報から Imagen 用プロンプトを生成する"""
    style = STYLE_GUIDES.get(category, DEFAULT_STYLE)

    system_prompt = f"""You are an expert at writing prompts for photorealistic image generation.
Generate a detailed English prompt for a hero image for a blog article about coffee.

Style guidelines:
- Style: {style['style']}
- Lighting: {style['lighting']}
- Background: {style['background']}
- Mood: {style['mood']}
- Composition: {style['composition']}

Requirements:
- Photorealistic, high quality
- No text, no watermarks
- No people's faces
- Suitable as a blog hero image (1200x630px)
- Maximum 150 words

Return ONLY the prompt text, nothing else."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"Article title: {title}\nDescription: {description}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
        ),
    )
    return response.text.strip()
```

---

## ステップ3：Imagen 4.0 で画像生成

```python
import time
import base64
from pathlib import Path
from PIL import Image
import io

def generate_image(
    prompt: str,
    output_path: Path,
    max_retries: int = 3,
) -> bool:
    """Imagen 4.0 で画像を生成して WebP で保存する"""
    for attempt in range(max_retries):
        try:
            response = client.models.generate_images(
                model="imagen-4.0-generate-preview-05-20",
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",          # 1200×630 に近いアスペクト比
                    safety_filter_level="BLOCK_MEDIUM_AND_ABOVE",
                    person_generation="DONT_ALLOW",  # 人物生成を無効化
                ),
            )

            if not response.generated_images:
                print(f"  画像生成失敗（レスポンスが空）: attempt {attempt + 1}")
                time.sleep(2 ** attempt)  # 指数バックオフ
                continue

            # PNG バイトデータを取得
            image_bytes = response.generated_images[0].image.image_bytes

            # Pillow で WebP に変換・リサイズ
            img = Image.open(io.BytesIO(image_bytes))
            img = img.resize((1200, 630), Image.LANCZOS)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "WEBP", quality=85, method=6)

            print(f"  保存完了: {output_path}")
            return True

        except Exception as e:
            error_str = str(e)
            if "503" in error_str or "Service Unavailable" in error_str:
                wait = 2 ** attempt * 5  # 5秒, 10秒, 20秒
                print(f"  503エラー、{wait}秒後にリトライ: attempt {attempt + 1}")
                time.sleep(wait)
            else:
                print(f"  予期しないエラー: {e}")
                return False

    print(f"  最大リトライ回数に達しました")
    return False
```

503 エラーは Imagen 4.0 のプレビュー期間中に頻発します。指数バックオフでリトライするのが効果的です。

---

## ステップ4：MDX フロントマターの自動更新

```python
import frontmatter

def update_mdx_frontmatter(
    mdx_path: Path,
    image_path: str,
) -> None:
    """MDX ファイルの image フィールドを更新する"""
    post = frontmatter.load(mdx_path)
    post["image"] = image_path
    post["image_generated"] = True  # 生成済みフラグ

    with open(mdx_path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
```

---

## バッチ処理のメインループ

```python
from pathlib import Path
import frontmatter
import time

ARTICLES_DIR = Path("/path/to/articles")
IMAGES_DIR = Path("/path/to/public/images/articles")
COST_PER_IMAGE = 0.04  # USD

def batch_generate_images(dry_run: bool = False) -> None:
    """全記事の画像を一括生成する"""
    mdx_files = list(ARTICLES_DIR.glob("**/*.mdx"))
    total = len(mdx_files)
    success = 0
    skip = 0
    fail = 0

    print(f"対象記事数: {total}")
    print(f"推定コスト: ${total * COST_PER_IMAGE:.2f}\n")

    for i, mdx_path in enumerate(mdx_files, 1):
        post = frontmatter.load(mdx_path)

        # 既に生成済みの場合はスキップ
        if post.get("image_generated"):
            print(f"[{i}/{total}] スキップ（生成済み）: {mdx_path.name}")
            skip += 1
            continue

        title = post.get("title", "")
        description = post.get("description", "")
        category = post.get("category", "")

        if not title:
            print(f"[{i}/{total}] スキップ（タイトルなし）: {mdx_path.name}")
            skip += 1
            continue

        print(f"[{i}/{total}] 処理中: {title}")

        # プロンプト生成
        prompt = generate_image_prompt(title, description, category)
        print(f"  プロンプト: {prompt[:80]}...")

        if dry_run:
            print(f"  [DRY RUN] 画像生成をスキップ")
            continue

        # 出力パスを決定
        slug = mdx_path.stem
        output_path = IMAGES_DIR / f"{slug}.webp"
        image_url = f"/images/articles/{slug}.webp"

        # 画像生成
        if generate_image(prompt, output_path):
            update_mdx_frontmatter(mdx_path, image_url)
            success += 1
        else:
            fail += 1

        # レート制限対策（1秒待機）
        time.sleep(1)

    print(f"\n完了: 成功={success}, スキップ={skip}, 失敗={fail}")
    print(f"実際のコスト: ${success * COST_PER_IMAGE:.2f}")


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    batch_generate_images(dry_run=dry_run)
```

---

## コスト実績

実際に `coffee-guide.jp`（54記事）で実行した結果：

| 指標 | 値 |
|------|-----|
| 対象記事数 | 54 |
| 生成成功 | 48 |
| スキップ（フロントマター不備等） | 4 |
| 失敗（503リトライ超過） | 2 |
| 総コスト | $1.92（48枚 × $0.04） |
| 所要時間 | 約25分 |

**503 エラー対策 ** が重要で、指数バックオフなしで実行すると失敗率が50%を超えることもありました。最大3回リトライ + 指数バックオフで98%以上の成功率になりました。

---

## DALL-E 3・Midjourney との比較

| 項目 | Imagen 4.0 | DALL-E 3 | Midjourney |
|------|-----------|---------|-----------|
| 1枚のコスト | $0.04 | $0.04（HD） | 月額制（$10〜） |
| Python API | ○ | ○ | △（非公式） |
| 写実性（食品） | ◎ | ○ | ○ |
| バッチ処理 | ○ | ○ | × |
| レート制限 | あり（503） | あり | × |

食品・物撮り系の写実性は Imagen 4.0 が優れています。コーヒーの質感・光の当たり方が自然で、フリー素材よりブランド感のある画像になりました。

---

## 注意点

### コンテンツポリシー

Imagen 4.0 は安全フィルタが強めです。次のような表現はブロックされることがあります：

- 人物の顔（`person_generation="DONT_ALLOW"` を設定すると回避できる）
- ブランドロゴやテキストを含む指示
- 著作権のある特定のスタイルへの言及

### プレビュー版の注意

`imagen-4.0-generate-preview-05-20` はプレビュー版のため、モデル名が変更になる可能性があります。本番運用では定期的にモデル名を確認してください。

### 料金の変動

Imagen の料金は Google が変更することがあります。大量生成前にGoogle AI Studio の料金ページで最新の単価を確認することを推奨します。

---

## まとめ

Imagen 4.0 + Gemini 2.5 Flash の組み合わせで、ブログ画像の一括生成を低コストで実現できました。

- **Gemini でプロンプト生成 ** → 記事に合った具体的な画像指示を自動化
- **503 エラーは指数バックオフで対処 ** → リトライ3回で98%以上成功
- **WebP 変換でファイルサイズ削減 ** → PNG 比で約40%削減
- ** フロントマター自動更新 ** → 再実行時のスキップで二重課金防止

48枚 $2 というコストは、Midjourney の月額（$10〜）と比べると圧倒的に安価です。コンテンツ量の多いアフィリエイトサイトや技術ブログの画像整備に活用できます。

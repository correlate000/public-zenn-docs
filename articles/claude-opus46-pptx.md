---
title: "Claude Opus 4.6 PPTX生成の仕組みを逆算する──Agentic AI設計パターンの実装ガイド"
emoji: "📊"
type: "tech"
topics: ["claude", "ai", "pptx", "agenticai", "llm"]
published: false
publication_name: "correlate_dev"
---

## はじめに：Claude Opus 4.6 の PPTX 生成機能がもたらしたもの

2026年1月、Anthropic が Claude Opus 4.6 をリリースしました。その中で最も注目すべき機能が **PowerPoint 統合** です。

Zenn に公開された記事「Claude Opus4.6はどのようにPPTXを生成しているか」（著者: 07JP27 氏）では、Claude が Web 検索 → スライド生成 → PDF 化 → 品質検証という 8 段階のワークフローを使用していることと読み取れます。

では、どのようにしてこのようなワークフロー が実現されているのか？

**本記事では、公開記事から逆算して、Opus 4.6 PPTX 生成の背後にある「Agentic AI 設計パターン」を解明します。** 実装例を通じて、他のLLMやタスクにも応用可能な汎用的なアーキテクチャを学ぶことができます。

---

## 1. Agentic AI の基本概念

### 従来の LLM API と Agentic ワークフローの違い

**従来の LLM API**:
```
User Input → Claude → Output
```

シンプルですが、限界があります：
- 外部データを取得できない（検索、API呼び出し）
- マルチステップのタスクに対応できない
- 自己修正ができない

**Agentic ワークフロー**:
```
User Input
  → Agent (判定)
  → Tool 1 (Web検索)
  → Tool 2 (スライド生成)
  → Tool 3 (PDF化)
  → Reflection (検証)
  → Output
```

複雑ですが、自律的なタスク解決が可能です。

### Agentic AI の3要素

1. **Agent**: タスクを分解し、どのツールを使うか判定
2. **Tool**: 外部API・ライブラリとの連携（Web検索、ファイル操作等）
3. **Reflection**: 生成結果を検証し、改善が必要なら再度実行

Claude Opus 4.6 PPTX 生成は、この3要素をシームレスに統合した例です。

---

## 2. 公開記事から読み取る PPTX 生成アーキテクチャ

### 8 ステップの逆算分析

公開記事では、Opus 4.6 が以下の 8 ステップを実行していることと推測されます：

```
Step 1: タスク受け取り
Step 2: Web検索（コンテンツ収集）
Step 3: スライド構成設計
Step 4: PptxGenJS による PPTX 生成
Step 5: PPTX → PDF 変換
Step 6: PDF → 画像 キャプチャ
Step 7: 画像 QA（品質検証）
Step 8: 最終調整・出力
```

### ステップ分解：技術スタック推定

| ステップ | 実装技術 | 役割 |
|--------|--------|------|
| Step 1 | LLM (Claude) | タスク理解、アウトラインの生成 |
| Step 2 | Web API / Bing Search | リアルタイム情報取得 |
| Step 3 | Claude (Reflection) | スライド構成の最適化 |
| Step 4 | PptxGenJS (Node.js) | Office Open XML 形式で生成 |
| Step 5 | LibreOffice / pypptx | PPTX → PDF 変換 |
| Step 6 | ImageMagick / Pillow | PDF → PNG/JPG 変換 |
| Step 7 | Claude (Vision) | 画像内容の品質検証 |
| Step 8 | Claude + User Feedback | 修正ループ |

このアーキテクチャは、**Agentic RAG（Retrieval-Augmented Generation）** の実装例そのものです。

---

## 3. Agentic RAG パターンの設計

### 従来の RAG と Agentic RAG の比較

**従来の RAG**:
```
Query
  → Vector Search
  → 検索結果を LLM に入力
  → Output
```

**Agentic RAG**:
```
Query
  → Agent が情報不足を判定
  → 複数の検索クエリを動的に生成
  → 検索・取得を反復実行
  → Reflection で信頼度を検証
  → 最終的に最高品質の Output を生成
```

### Agentic RAG の利点

1. **適応的情報収集**: クエリに応じて、検索戦略を動的に変更
2. **品質検証ループ**: 生成結果を自動検証し、不十分なら追加検索
3. **複雑なタスク対応**: マルチステップのワークフローに対応

Claude Opus 4.6 の PPTX 生成は、この Agentic RAG パターンを採用しています。

---

## 4. 実装パターン詳細

### パターン1: Web 検索 × 情報収集

```javascript
// Node.js 実装例
const axios = require('axios');

async function searchWeb(query) {
  // Bing Search API で情報取得
  const response = await axios.get('https://api.bing.microsoft.com/v7.0/search', {
    params: { q: query },
    headers: { 'Ocp-Apim-Subscription-Key': process.env.BING_API_KEY }
  });

  return response.data.webPages.value.slice(0, 5); // 上位 5 件
}

// 使用例
const articles = await searchWeb('最新の AI トレンド 2026');
```

### パターン2: PptxGenJS でのスライド生成

```javascript
const PptxGenJS = require("pptxgenjs");

const prs = new PptxGenJS();

// スライド 1: タイトル
let slide1 = prs.addSlide();
slide1.background = { color: "003366" };
slide1.addText("AI Trends 2026", {
  x: 0.5, y: 2.3, w: 9, h: 1,
  fontSize: 44, bold: true, color: "FFFFFF"
});

// スライド 2: 内容
let slide2 = prs.addSlide();
slide2.addText("Main Trends", { x: 0.5, y: 0.5, w: 9, h: 0.5, fontSize: 28 });
slide2.addText("Agentic AI", { x: 0.5, y: 1.2, w: 9, h: 0.4, bullet: true, fontSize: 18 });
slide2.addText("Multimodal Models", { x: 0.5, y: 1.6, w: 9, h: 0.4, bullet: true, fontSize: 18 });
slide2.addText("Cost Optimization", { x: 0.5, y: 2.0, w: 9, h: 0.4, bullet: true, fontSize: 18 });

prs.writeFile({ fileName: "presentation.pptx" });
```

### パターン3: PDF → 画像 QA ループ

```python
# Python 実装
from pdf2image import convert_from_path
from anthropic import Anthropic

def validate_slide_quality(pptx_path):
    """
    PPTX → PDF → 画像 に変換して、品質検証
    """
    # 1. PPTX を PDF に変換（LibreOffice）
    os.system(f'libreoffice --headless --convert-to pdf {pptx_path}')
    pdf_path = pptx_path.replace('.pptx', '.pdf')

    # 2. PDF → 画像に変換
    images = convert_from_path(pdf_path, first_page=1, last_page=5)

    # 3. Claude Vision で品質検証
    client = Anthropic()
    for i, image in enumerate(images):
        with open(f"slide_{i}.png", "wb") as f:
            image.save(f)

        with open(f"slide_{i}.png", "rb") as img_file:
            image_data = base64.standard_b64encode(img_file.read()).decode("utf-8")

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "このスライドの品質を評価してください。改善点があれば指摘してください。"
                    }
                ],
            }],
        )

        print(f"Slide {i} Quality: {response.content[0].text}")
```

### パターン4: 完全な Agentic パイプライン

```python
class AgenticPPTXGenerator:
    def __init__(self):
        self.client = Anthropic()
        self.max_iterations = 3

    def generate(self, topic: str):
        # Step 1: Web検索で情報収集
        search_results = self.web_search(topic)

        # Step 2: スライド構成設計
        outline = self.design_outline(topic, search_results)

        # Step 3: PPTX 生成
        pptx_path = self.create_pptx(outline)

        # Step 4: 品質検証ループ
        for iteration in range(self.max_iterations):
            quality_feedback = self.validate_quality(pptx_path)

            if "改善不要" in quality_feedback:
                break

            # Step 5: 改善実行
            outline = self.refine_outline(outline, quality_feedback)
            pptx_path = self.create_pptx(outline)

        return pptx_path

    def web_search(self, query: str):
        """Web から情報取得"""
        # Bing Search API or Google Custom Search API
        pass

    def design_outline(self, topic: str, search_results: list):
        """Claude に スライド構成を設計させる"""
        search_summary = '\n'.join([r['snippet'] for r in search_results[:3]])
        prompt = f"""
        以下のトピックで PowerPoint のアウトラインを設計してください。
        トピック: {topic}

        参考情報:
        {search_summary}

        出力フォーマット:
        # タイトル: ...
        ## スライド 1: ...
        - ポイント 1
        - ポイント 2
        ...
        """

        response = self.client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text

    def create_pptx(self, outline: str):
        """PptxGenJS で PPTX を生成"""
        # Node.js への API 呼び出し
        pass

    def validate_quality(self, pptx_path: str):
        """Claude Vision で品質検証"""
        # PDF → 画像変換 → Claude Vision
        pass

    def refine_outline(self, outline: str, feedback: str):
        """フィードバックに基づいてアウトラインを改善"""
        pass
```

---

## 5. 実装例：Node.js での完全なコード

### 環境構築

```bash
npm init -y
npm install pptxgenjs axios dotenv
```

### main.js

```javascript
const PptxGenJS = require("pptxgenjs");
const axios = require('axios');
require('dotenv').config();

async function generatePresentation(topic) {
  // Step 1: Web検索
  console.log("Fetching information...");
  const articles = await searchWeb(topic);

  // Step 2: スライド構成設計
  const outline = await designOutline(topic, articles);

  // Step 3: PPTX生成
  const prs = new PptxGenJS();
  const slides = parseOutline(outline);

  for (const slide of slides) {
    let pptxSlide = prs.addSlide();

    if (slide.type === 'title') {
      pptxSlide.background = { color: "003366" };
      pptxSlide.addText(slide.content, {
        x: 0.5, y: 2.3, w: 9, h: 1,
        fontSize: 44, bold: true, color: "FFFFFF"
      });
    } else if (slide.type === 'content') {
      pptxSlide.addText(slide.title, {
        x: 0.5, y: 0.5, w: 9, h: 0.5,
        fontSize: 28, bold: true
      });

      slide.bullets.forEach((bullet, index) => {
        pptxSlide.addText(bullet, {
          x: 0.5, y: 1.2 + (0.4 * index), w: 9, h: 0.4,
          bullet: true, fontSize: 18
        });
      });
    }
  }

  prs.writeFile({ fileName: `${topic}.pptx` });
  console.log("PPTX generated successfully!");

  return `${topic}.pptx`;
}

async function searchWeb(query) {
  // Bing Search API example
  const response = await axios.get('https://api.bing.microsoft.com/v7.0/search', {
    params: { q: query },
    headers: { 'Ocp-Apim-Subscription-Key': process.env.BING_API_KEY }
  });

  return response.data.webPages.value;
}

async function designOutline(topic, articles) {
  // Claude API call (pseudocode)
  // Actual implementation would use Anthropic SDK
  return `# ${topic}\n## Overview\n- Point 1\n- Point 2`;
}

function parseOutline(outline) {
  // Parse outline into slide structure
  const lines = outline.split('\n');
  const slides = [];

  for (const line of lines) {
    if (line.startsWith('# ')) {
      slides.push({ type: 'title', content: line.replace('# ', '') });
    } else if (line.startsWith('## ')) {
      slides.push({ type: 'content', title: line.replace('## ', ''), bullets: [] });
    } else if (line.startsWith('- ')) {
      if (slides.length > 0) {
        slides[slides.length - 1].bullets.push(line.replace('- ', ''));
      }
    }
  }

  return slides;
}

// Run
generatePresentation("AI Trends 2026");
```

---

## 6. 実践例：Agentic PPTXパイプラインの全体像

Agentic PPTXパイプラインを実際のシステムに組み込む場合、以下のような構成が典型的です：

```
User: "営業提案資料を作成してください"
  ↓
① タスク理解（Agent）
  → トピック：営業提案
  → 要件：顧客向け、3-5スライド
  ↓
② 情報収集（Tool: Web/DB）
  → 過去の提案テンプレート検索
  → 競合情報取得
  ↓
③ スライド設計（Agent: Claude）
  → 構成案作成
  → コンテンツアウトライン設計
  ↓
④ PPTX生成（Tool: PptxGenJS）
  → スライド生成
  ↓
⑤ 品質検証（Reflection: Claude Vision）
  → PDF化 → 画像キャプチャ
  → 品質評価
  ↓
⑥ 最終調整（Agent）
  → ユーザーフィードバック反映
  ↓
Output: presentation.pptx
```

この設計により、**Agentic PPTX 生成**パイプラインを構築できます。各ステップが独立したモジュールとして機能するため、特定のステップをカスタマイズしたり、他のドキュメント形式（PDF、Word等）に差し替えることも容易です。

---

## 7. Agentic パターンの汎化：他のタスクへの応用

### 応用例1: 自動レポート生成

```
データ（CSV）
  → Agent: 分析方針決定
  → Tool: グラフ生成
  → Tool: テキスト生成
  → Reflection: 品質検証
  → Output: PDF レポート
```

### 応用例2: 自動テスト生成（Reflection活用）

```
ソースコード
  → Agent: テストケース設計
  → Tool: テストコード生成
  → Tool: テスト実行
  → Reflection: 失敗原因分析
  → Tool: テストコード修正
  → Output: テストスイート
```

### 応用例3: 多言語翻訳＋品質検証

```
日本語テキスト
  → Tool: 機械翻訳（DeepL）
  → Reflection: 品質検証（Claude）
  → 低品質なら修正
  → Output: 多言語テキスト
```

---

## まとめ：Agentic AI は LLM の使い方の革新

Claude Opus 4.6 の PPTX 生成機能は、単なる新機能ではありません。これは **Agentic AI がどのように複雑なタスクを自律的に解決するか** の実装例です。

### Agentic パターンの要点

1. **Agent**: タスク分解と判定ロジック
2. **Tool**: 外部API・ライブラリの統合
3. **Reflection**: 自己検証と改善ループ

この3つの要素を組み合わせることで、従来のプロンプトエンジニアリングでは実現不可能な、**自律的でロバストなAIシステム** が実現できます。

### 今後の展開

2026年現在、**「Agentic が当たり前」の時代** が当たり前になりつつあります。

- **生産性ツール**: Agentic PPTX生成、Agentic レポート作成
- **開発支援**: Agentic テスト生成、Agentic バグ修正
- **データ分析**: Agentic データクリーニング、Agentic インサイト生成

すべてが、今回解説した Agent → Tool → Reflection のパターンで実装されます。

このパターンを習得することが、次世代のAIエンジニアとしてのスキルセットになるでしょう。

---

## 参考資料

- [Claude Opus4.6はどのようにPPTXを生成しているか](https://zenn.dev/microsoft/articles/how-the-claude-opus46-generate-pptx)
- [Agentic RAGとは？AIエージェントでRAGを強化する実践ガイド](https://arpable.com/artificial-intelligence/rag/agentic-rag-tools-2025/)
- [PptxGenJS - GitHub](https://github.com/gitbrent/PptxGenJS)
- [Anthropic API Documentation](https://docs.anthropic.com)
- [IBM Think - Agentic RAG](https://www.ibm.com/think/topics/agentic-rag)

---
title: "MDXコンポーネントに構造化データを埋め込んでSEOリッチスニペットを自動獲得する"
emoji: "🔖"
type: "tech"
topics: ["nextjs", "mdx", "seo", "jsonld", "typescript"]
published: false
status: "publish-ready"
publication_name: "correlate_dev"
---

Next.js + MDX で構築したコンテンツサイトを運営していると、「JSON-LD の構造化データを各記事に入れたい」という場面に必ず当たります。

ところが、よくある解決策は「ページコンポーネントで `<script type="application/ld+json">` を出力する」か「`next-seo` を使う」の二択です。どちらも機能しますが、 **記事を書く人が JSON-LD の存在を意識しなければならない** という問題が残ります。

本記事では別のアプローチを紹介します。MDX の特性を活かして、 **コンポーネントに props を渡すだけで JSON-LD が自動生成される** 設計です。コーヒー情報サイト（Next.js 16 + next-mdx-remote + Tailwind CSS v4）の実装で動作を確認しており、HowTo・Recipe・FAQPage・Review の 4 種類を例に解説します。

## なぜコンポーネントに埋め込むのか

MDX の強みは「Markdown 記事の中で React コンポーネントを呼べる」点です。

```mdx
## ドリップの手順

<BrewingGuide
  method="ハンドドリップ"
  totalTime="4分"
  steps="お湯を沸かす:1分|コーヒー粉をセット:30秒|お湯を注ぐ:2分30秒"
/>
```

このように書くと、BrewingGuide コンポーネントが視覚的な手順カードを表示します。 **ここに JSON-LD 出力を同居させる** と、記事作成者は props を埋めるだけで構造化データが完成します。

### 従来手法との比較

| 手法 | JSON-LD の管理 | MDX との統合 | 実装コスト |
|------|---------------|-------------|-----------|
| ページコンポーネントで出力 | ページごとに手動 | 疎結合 | 低（単純） |
| next-seo | フロントマターで管理 | 疎結合 | 低（ライブラリ依存） |
| MDX コンポーネントに埋め込む | props から自動生成 | 密結合 | 中（コンポーネント設計が必要） |

密結合であることが本手法の価値です。コンポーネントを置けば必ず構造化データが生成されるため、設置漏れが発生しません。

## 実装の基本パターン

すべてのパターンに共通する実装構造を確認します。

```tsx
export function SomeComponent({ ...props }) {
  // props から JSON-LD オブジェクトを組み立てる
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SomeType",
    // ...
  };

  return (
    <div>
      {/* JSON-LD を script タグで出力 */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/<\/script>/gi, "<\\/script>"),
        }}
      />
      {/* 通常の UI */}
      <div>...</div>
    </div>
  );
}
```

`dangerouslySetInnerHTML` で出力する際、JSON 内に `</script>` という文字列が含まれると HTML パーサーがスクリプトタグの終端と誤認します。そのため `.replace(/<\/script>/gi, "<\\/script>")` でエスケープします。これは省略できない処理です。

## パターン 1: HowTo（BrewingGuide コンポーネント）

手順記事に使う `HowTo` スキーマです。Google 検索では「リッチスニペット → ハウツー」として表示されることがあります。

### ISO 8601 Duration の変換

`totalTime` は ISO 8601 の Duration 形式（`PT4M`、`PT1H30M` など）で指定する必要があります。しかし MDX 内で「4分」と書けるほうが自然です。そこで変換関数を用意します。

```tsx
function parseDuration(raw: string): string | null {
  const hours = raw.match(/(\d+)\s*(?:時間|hours?|hrs?)/i);
  const mins  = raw.match(/(\d+)\s*(?:分|minutes?|min)/i);
  const secs  = raw.match(/(\d+)\s*(?:秒|seconds?|sec)/i);
  if (!hours && !mins && !secs) return null;
  let iso = "PT";
  if (hours) iso += `${hours[1]}H`;
  if (mins)  iso += `${mins[1]}M`;
  if (secs)  iso += `${secs[1]}S`;
  return iso === "PT" ? null : iso;
}
```

**落とし穴**: 実装初期に `replace(/[^0-9]/g, "")` で数字だけ取り出す方法を試しました。「3分30秒」を変換すると `330` が残り、`PT330M`（330分）という不正値になります。時・分・秒を個別の正規表現でキャプチャする方法が正解です。

範囲値（「12〜24時間」）は先頭の数値のみ取得します（`12` → `PT12H`）。Google は最大値より最小値を好む傾向があるため、この仕様で問題ありません。

### コンポーネントの全体像

```tsx
interface BrewingGuideProps {
  method: string;
  totalTime?: string;
  steps?: string;  // "アクション:所要時間|アクション:所要時間" 形式
  locale?: string;
}

export function BrewingGuide({
  method,
  totalTime,
  steps,
  locale = "ja",
}: BrewingGuideProps) {
  const stepList =
    steps?.split("|").map((s) => {
      const [action, duration] = s.split(":");
      return { action: action?.trim(), duration: duration?.trim() };
    }) ?? [];

  const jsonLd = stepList.length > 0
    ? {
        "@context": "https://schema.org",
        "@type": "HowTo",
        name: method,
        ...(totalTime && parseDuration(totalTime)
          ? { totalTime: parseDuration(totalTime) }
          : {}),
        step: stepList.map((step, i) => ({
          "@type": "HowToStep",
          position: i + 1,
          text: step.action,
          name: step.duration
            ? `${step.action} (${step.duration})`
            : step.action,
        })),
      }
    : null;

  return (
    <div className="my-6 border rounded-lg overflow-hidden">
      {jsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(jsonLd).replace(/<\/script>/gi, "<\\/script>"),
          }}
        />
      )}
      <div className="bg-amber-700 text-white px-5 py-3 flex items-center justify-between">
        <h4 className="font-semibold">{method}</h4>
        {totalTime && (
          <span className="text-sm opacity-90">
            {locale === "ja" ? `合計 ${totalTime}` : `Total ${totalTime}`}
          </span>
        )}
      </div>
      <div className="p-5 space-y-3">
        {stepList.map((step, i) => (
          <div key={i} className="flex items-start gap-3">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-amber-700 text-white flex items-center justify-center text-sm font-bold">
              {i + 1}
            </div>
            <div className="flex-1 pt-1">
              <p className="text-sm font-medium">{step.action}</p>
              {step.duration && (
                <p className="text-xs text-gray-500 mt-0.5">{step.duration}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

### MDX での記述例

```mdx
<BrewingGuide
  method="ハンドドリップ（2杯分）"
  totalTime="4分"
  steps="お湯を95℃に沸かす:1分|コーヒー粉をフィルターにセット:30秒|蒸らし用のお湯を注ぐ:30秒|3回に分けてお湯を注ぐ:2分"
/>
```

生成される JSON-LD（抜粋）:

```json
{
  "@context": "https://schema.org",
  "@type": "HowTo",
  "name": "ハンドドリップ（2杯分）",
  "totalTime": "PT4M",
  "step": [
    { "@type": "HowToStep", "position": 1, "text": "お湯を95℃に沸かす", "name": "お湯を95℃に沸かす (1分)" },
    { "@type": "HowToStep", "position": 2, "text": "コーヒー粉をフィルターにセット", "name": "コーヒー粉をフィルターにセット (30秒)" }
  ]
}
```

## パターン 2: Recipe（RecipeCard コンポーネント）

コーヒードリンクのレシピ記事に使う `Recipe` スキーマです。材料・手順・分量を props で受け取り、構造化データと UI を同時に出力します。

```tsx
interface RecipeCardProps {
  title: string;
  difficulty?: string;
  time?: string;
  servings?: string;
  ingredients?: string;  // "材料名:分量|材料名:分量" 形式
  steps?: string;        // "手順1|手順2" 形式
  locale?: string;
}

export function RecipeCard({
  title, difficulty, time, servings,
  ingredients, steps, locale = "ja",
}: RecipeCardProps) {
  const ingredientList = ingredients?.split("|").filter(Boolean) ?? [];
  const stepList = steps?.split("|").filter(Boolean) ?? [];

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    name: title,
    ...(time && parseDuration(time) ? { totalTime: parseDuration(time) } : {}),
    ...(servings ? { recipeYield: servings } : {}),
    recipeIngredient: ingredientList.map((item) => {
      const [name, amount] = item.split(":");
      return amount ? `${name.trim()} ${amount.trim()}` : name.trim();
    }),
    recipeInstructions: stepList.map((step, i) => ({
      "@type": "HowToStep",
      position: i + 1,
      text: step.trim(),
    })),
    recipeCategory: "Coffee",
    author: { "@type": "Organization", name: "Coffee Guide" },
  };

  return (
    <div className="my-6 border rounded-lg overflow-hidden">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/<\/script>/gi, "<\\/script>"),
        }}
      />
      <div className="bg-emerald-700 text-white px-5 py-3">
        <h4 className="font-semibold">{title}</h4>
        <div className="flex gap-4 text-sm mt-1 opacity-90">
          {difficulty && <span>{difficulty}</span>}
          {time && <span>⏱ {time}</span>}
          {servings && <span>{servings}</span>}
        </div>
      </div>
      <div className="p-5 grid md:grid-cols-2 gap-6">
        {ingredientList.length > 0 && (
          <div>
            <h5 className="font-semibold text-sm text-emerald-700 mb-2">
              {locale === "ja" ? "材料" : "Ingredients"}
            </h5>
            <ul className="space-y-1">
              {ingredientList.map((item, i) => {
                const [name, amount] = item.split(":");
                return (
                  <li key={i} className="text-sm flex justify-between">
                    <span>{name?.trim()}</span>
                    {amount && (
                      <span className="text-gray-500">{amount.trim()}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {stepList.length > 0 && (
          <div>
            <h5 className="font-semibold text-sm text-emerald-700 mb-2">
              {locale === "ja" ? "手順" : "Steps"}
            </h5>
            <ol className="space-y-2">
              {stepList.map((step, i) => (
                <li key={i} className="text-sm flex gap-2">
                  <span className="font-bold flex-shrink-0">{i + 1}.</span>
                  <span>{step.trim()}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}
```

### MDX での記述例

```mdx
<RecipeCard
  title="カフェラテ"
  difficulty="初級"
  time="5分"
  servings="1杯"
  ingredients="エスプレッソ:30ml|牛乳:150ml|砂糖:お好みで"
  steps="エスプレッソを抽出する|牛乳を60℃程度に温めてフォームを作る|エスプレッソの上にゆっくり注ぐ"
/>
```

生成される `recipeIngredient` の値は `["エスプレッソ 30ml", "牛乳 150ml", "砂糖 お好みで"]` という文字列配列になります。Google が認識しやすいフォーマットです。

## パターン 3: FAQPage（Faq コンポーネント）

FAQ アコーディオンに `FAQPage` スキーマを組み込みます。Google 検索での「よくある質問」表示を狙えるため、効果が高いスキーマの一つです。

```tsx
interface FaqItem {
  question: string;
  answer: string;
}

interface FaqProps {
  items: string;  // "質問1|回答1||質問2|回答2" 形式
  locale?: string;
}

export function Faq({ items, locale = "ja" }: FaqProps) {
  const parsed: FaqItem[] = items
    .split("||")
    .map((pair) => {
      const pipeIdx = pair.indexOf("|");
      if (pipeIdx === -1) return null;
      const question = pair.slice(0, pipeIdx).trim();
      const answer   = pair.slice(pipeIdx + 1).trim();
      return question && answer ? { question, answer } : null;
    })
    .filter((item): item is FaqItem => item !== null);

  if (parsed.length === 0) return null;

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: parsed.map((item) => ({
      "@type": "Question",
      name: item.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: item.answer,
      },
    })),
  };

  return (
    <div className="my-10">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/<\/script>/gi, "<\\/script>"),
        }}
      />
      <h2 className="text-xl font-bold mb-4">
        {locale === "ja" ? "よくある質問" : "Frequently Asked Questions"}
      </h2>
      <div className="space-y-3">
        {parsed.map((item, i) => (
          <details
            key={i}
            className="group border rounded-lg overflow-hidden"
          >
            <summary className="flex items-center justify-between cursor-pointer px-5 py-4 font-semibold hover:bg-gray-50">
              <span className="flex items-center gap-3">
                <span className="flex-shrink-0 w-6 h-6 bg-amber-500 rounded-full flex items-center justify-center text-xs font-bold text-white">
                  Q
                </span>
                {item.question}
              </span>
              <svg
                width="16" height="16" viewBox="0 0 24 24"
                fill="none" stroke="currentColor" strokeWidth="2"
                className="flex-shrink-0 transition-transform group-open:rotate-180"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </summary>
            <div className="px-5 pb-4 pt-3 text-sm text-gray-600 border-t ml-9">
              {item.answer}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
```

### 区切り文字の設計

Q&A の区切りに `||`（ダブルパイプ）を使い、質問と回答の区切りに `|`（シングルパイプ）を使います。回答文の中に `|` が含まれる可能性があるため、`indexOf` で最初の `|` だけを区切りとして扱います。`split("|")` では不十分なので注意が必要です。

```mdx
<Faq items="ハンドドリップとドリップバッグの違いは？|ハンドドリップは器具と技術が必要ですが、豆の鮮度や粗さを調整できます。ドリップバッグは手軽さが魅力です。||コーヒーの適切な保存方法は？|焙煎後2週間以内を目安に、常温の密閉容器で保存します。冷凍保存は結露が生じるためおすすめしません。" />
```

## パターン 4: Review（VerdictBox コンポーネント）

レビュー記事の総合評価ボックスに `Review` スキーマを付与します。

```tsx
interface VerdictBoxProps {
  score: string;        // "8.5" のような文字列（MDX では数値リテラルでなく文字列として渡される）
  categories?: string;  // "味:9|コスパ:7|使いやすさ:8" 形式
  summary?: string;
  locale?: string;
}

export function VerdictBox({ score, categories, summary, locale = "ja" }: VerdictBoxProps) {
  const numScore = parseFloat(score) || 0;
  const catList  = categories
    ?.split("|")
    .map((c) => {
      const [label, val] = c.split(":");
      return { label: label?.trim() || "", value: parseFloat(val) || 0 };
    })
    .filter((c) => c.label) ?? [];

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Review",
    reviewRating: {
      "@type": "Rating",
      ratingValue: numScore.toString(),
      bestRating: "10",
      worstRating: "0",
    },
    author: { "@type": "Organization", name: "Coffee Guide" },
  };

  const starColor =
    numScore >= 8 ? "text-green-600" :
    numScore >= 6 ? "text-yellow-600" : "text-red-500";

  return (
    <div className="my-8 border-2 border-amber-500 rounded-xl overflow-hidden shadow-sm">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/<\/script>/gi, "<\\/script>"),
        }}
      />
      <div className="bg-amber-500 text-white px-5 py-3 flex items-center justify-between">
        <h4 className="font-bold">
          {locale === "ja" ? "総合評価" : "Overall Rating"}
        </h4>
        <div className="flex items-center gap-2">
          <span className="text-3xl font-black">{numScore.toFixed(1)}</span>
          <span className="text-sm opacity-80">/ 10</span>
        </div>
      </div>
      <div className="p-5 space-y-4">
        {catList.length > 0 && (
          <div className="space-y-2.5">
            {catList.map((cat, i) => {
              const pct   = Math.min(cat.value * 10, 100);
              const color = cat.value >= 8 ? "bg-green-500" : cat.value >= 6 ? "bg-yellow-500" : "bg-red-400";
              return (
                <div key={i} className="flex items-center gap-3">
                  <span className="text-xs text-gray-500 w-24 flex-shrink-0 text-right">{cat.label}</span>
                  <div className="flex-1 bg-gray-100 rounded-full h-2.5 overflow-hidden">
                    <div className={`${color} h-full rounded-full`} style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-xs font-bold w-8">{cat.value.toFixed(1)}</span>
                </div>
              );
            })}
          </div>
        )}
        {summary && (
          <div className={`text-sm ${starColor} font-medium border-t pt-3 mt-3`}>
            {summary}
          </div>
        )}
      </div>
    </div>
  );
}
```

```mdx
<VerdictBox
  score="8.5"
  categories="味の深さ:9|コスパ:7.5|豆の鮮度:8.5|入手しやすさ:9"
  summary="価格帯を考えると品質が高く、毎日飲みたい1杯を作れるコーヒー豆です。"
/>
```

## next-mdx-remote への登録

各コンポーネントを MDX ファイルから使えるようにするには、`MDXRemote` に渡す `components` オブジェクトへ登録します。

```tsx
// src/components/mdx/index.tsx
import { BrewingGuide } from "./brewing-guide";
import { RecipeCard }   from "./recipe-card";
import { Faq }          from "./faq";
import { VerdictBox }   from "./verdict-box";

export const mdxComponents = {
  BrewingGuide,
  RecipeCard,
  Faq,
  VerdictBox,
};
```

```tsx
// src/app/[locale]/[category]/[slug]/page.tsx（抜粋）
import { MDXRemote } from "next-mdx-remote/rsc";
import { mdxComponents } from "@/components/mdx";

export default async function ArticlePage({ params }) {
  const { content, frontmatter } = await getArticle(params);

  return (
    <article>
      <MDXRemote source={content} components={mdxComponents} />
    </article>
  );
}
```

next-mdx-remote v5 以降は RSC（React Server Components）に対応しています。`MDXRemote` をそのまま async コンポーネントとして使える点が特徴です。

## 注意点と検証方法

### 1 ページに複数の JSON-LD が出力される場合

1 つの記事ページに `BrewingGuide` と `Faq` が両方存在すると、`<script type="application/ld+json">` タグが複数出力されます。これは仕様上問題ありません。Google は同一ページに複数の構造化データブロックを解釈できます。ただし、同じ `@type` を複数持つ場合は意図した表示にならないこともあるため、1 ページにつき同一スキーマは 1 つを目安にします。

### フロントマターからの JSX 式評価は機能しない

next-mdx-remote では `{frontmatter.title}` のような JSX 式を MDX ファイル内で評価できません。コンパイル時にエラーにはなりませんが、undefined が返ります。構造化データのフィールドをフロントマターから取得したい場合は、ページコンポーネント側で処理してからコンポーネントの props に渡す必要があります。

```tsx
// フロントマター値を props として明示的に渡す
<VerdictBox score={frontmatter.score} summary={frontmatter.summary} />
```

ただしこの記法は MDX ファイル内ではなく、ページコンポーネント（TSX）側の話になります。MDX ファイル内に書く場合はリテラル値のみ使用します。

### Google のリッチリザルトテストで検証する

実装後は Google のリッチリザルトテストで確認します。

1. デプロイ済みページの URL を入力（ローカルサーバーは不可）
2. 検出された構造化データの一覧が表示される
3. エラーや警告がなければ審査通過

ステージング URL でも検証できます。本番公開前のチェックに活用してください。

Google Search Console の「拡張」メニューからも確認できます。こちらはインデックス済みページの構造化データエラーを一覧で把握できるため、大量の記事を一括チェックする際に便利です。

## まとめ

本記事で解説した手法を整理します。

| コンポーネント | Schema.org タイプ | 主な効果 |
|--------------|------------------|---------|
| BrewingGuide | HowTo | ハウツースニペット |
| RecipeCard | Recipe | レシピカード表示 |
| Faq | FAQPage | よくある質問表示 |
| VerdictBox | Review | レビュー評価スター |

コンポーネントに JSON-LD を埋め込む設計には、「記事作成者が構造化データを意識しなくてよい」という最大のメリットがあります。コンポーネントを設置した時点で自動的にリッチスニペット獲得の可能性が生まれます。

実運用での注意点は 2 点です。まず、ISO 8601 Duration の変換は独自実装が必要で、素朴な数字抽出では複合値（「3分30秒」など）を誤変換します。次に、next-mdx-remote ではフロントマター変数の評価ができないため、MDX ファイル内の props はリテラル値で記述します。

この設計を採用してから、コーヒー情報サイトの 150 本以上の記事に構造化データが自動的に付与されました。大量記事を管理する MDX サイトでは、コンポーネント設計の段階で SEO 要件を組み込む方法が効率的です。

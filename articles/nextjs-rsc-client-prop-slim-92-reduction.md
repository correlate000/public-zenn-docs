---
title: "Next.js 16でclient componentのRSC propを92%削減するwrapper pattern"
emoji: "⚡"
type: "tech"
topics: ["nextjs", "react", "rsc", "performance", "typescript"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## 1. 問題の発見 — 自治体ページが 2MB を返していた

マチカルテ（machikarte.isvd.or.jp）は、全国の自治体ごとに健康スコアや施策一覧を表示する Next.js 16 製のサービスです。ある日、東京都豊島区のページを Chrome DevTools で確認したところ、HTTP レスポンスが **2,006,236 bytes（約 2MB）** あることに気づきました。gzip 圧縮後でも 335KB です。

静的コンテンツが中心のページでこのサイズは異常です。計測スクリプトを書いて原因を掘り下げました。

```python
import re
import json

with open("toshima_raw.html", "r", encoding="utf-8") as f:
    html = f.read()

# Next.js が埋め込む RSC チャンクを抽出
rsc_chunks = re.findall(r'self\.__next_f\.push\(\[.*?\]\)', html, re.DOTALL)

total_rsc_size = sum(len(chunk.encode("utf-8")) for chunk in rsc_chunks)
total_size = len(html.encode("utf-8"))

print(f"HTML 総サイズ: {total_size:,} bytes")
print(f"RSC チャンク計: {total_rsc_size:,} bytes")
print(f"RSC 比率: {total_rsc_size / total_size * 100:.1f}%")
```

出力結果は衝撃的でした。

```
HTML 総サイズ: 2,006,236 bytes
RSC チャンク計: 1,905,924 bytes
RSC 比率: 95.0%
```

HTML の **95% が RSC チャンク** です。ページ本体のコンテンツではなく、React が hydration のために埋め込む props データが全体を支配していました。

## 2. RSC serialization とは何か

Next.js の App Router では、Server Component がクライアントコンポーネントに props を渡すとき、その props は **RSC payload** としてシリアライズされ、HTML に埋め込まれます。

```
Server Component (サーバー)
    ↓ props を JSON シリアライズ
RSC payload (HTML に埋め込み)
    ↓ ブラウザがパース
Client Component (ブラウザ)
    ↓ hydration
インタラクティブなUI
```

この仕組みはサーバー・クライアント間のデータ転送を自動化する優れた設計ですが、**props に巨大な配列を渡すと、その全データが HTML に埋め込まれる** という副作用があります。

Next.js の HTML ソースを見ると、次のようなパターンが繰り返し現れます。

```html
<script>
  self.__next_f.push([1, "{\"kentou_items\":[{\"id\":1,\"title\":\"...\",\"score\":...},...],...}"])
</script>
```

`kentou_items` は施策検討項目の配列、`sessions` は会議記録の配列です。どちらも自治体ごとに数百件のデータを持ちます。

## 3. データサイズの内訳を計測する

RSC チャンク全体の中で、どのキーが重いかを特定します。

```python
import re
import json

with open("toshima_raw.html", "r", encoding="utf-8") as f:
    html = f.read()

# RSC チャンクから JSON 部分を抽出
pattern = r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)'
matches = re.findall(pattern, html)

key_sizes: dict[str, int] = {}

for match in matches:
    try:
        # エスケープを解除して JSON をパース
        raw = match.encode("utf-8").decode("unicode_escape")
        data = json.loads(raw)
        if isinstance(data, dict):
            for key, value in data.items():
                size = len(json.dumps(value).encode("utf-8"))
                key_sizes[key] = key_sizes.get(key, 0) + size
    except Exception:
        pass

for key, size in sorted(key_sizes.items(), key=lambda x: -x[1])[:10]:
    print(f"{key}: {size:,} bytes ({size / 1024:.1f} KB)")
```

実測値（豊島区ページ）:

| キー | サイズ |
|------|--------|
| kentou_items | 740,291 bytes (723 KB) |
| sessions | 200,847 bytes (196 KB) |
| score_history | 18,432 bytes (18 KB) |
| municipality_info | 4,210 bytes (4 KB) |
| その他合計 | 約 12 KB |

**`kentou_items` だけで 723KB**。施策検討項目と会議記録の 2 つで全体の 97% 近くを占めていました。

## 4. 選択肢の比較

原因は判明しました。次は対策の設計です。

### 選択肢 A: ScoreCard 本体を書き換える

`ScoreCard` は 1,586 行のクライアントコンポーネントです。内部でタブ切り替え、フィルタリング、ソートなどの複雑な状態管理をしています。このコンポーネントを書き換えて「重いデータは自前で fetch する」ように改修するのが最もクリーンな設計です。

ただし、リグレッションリスクが高い。既存のユニットテストは props を直接渡す形式で書かれており、fetch ロジックを混入させると全テストの見直しが必要になります。

### 選択肢 B: wrapper component で props を差し替える（採用）

`ScoreCard` 本体には一切触れず、外側に薄い wrapper を被せます。wrapper の役割は 2 つです。

1. Server Component から受け取った重い配列を **空配列に差し替えて** `ScoreCard` に渡す
2. クライアント側で `useEffect` を使って API から full data を fetch し、state をマージする

`ScoreCard` から見ると「props が来ている」という事実は変わりません。空配列で初期描画し、その後データが届いたら再レンダリングするだけです。**リグレッションリスクはゼロ** 。

| 項目 | 選択肢 A | 選択肢 B |
|------|----------|----------|
| 実装規模 | 1,586 行の改修 | 新規 30 行 |
| リグレッションリスク | 高 | ゼロ |
| ScoreCard 本体の変更 | あり | なし |
| 適用後の UX | 初回から full data | 初回 light、後追い full |

## 5. 実装: ScoreCardHydrator.tsx

```tsx
// src/components/scorecard/ScoreCardHydrator.tsx
"use client";

import { useState, useEffect } from "react";
import { ScoreCard } from "./ScoreCard";
import type { ScoreCardProps } from "./ScoreCard";

type HydratorProps = Omit<ScoreCardProps, "kentou_items" | "sessions"> & {
  municipalityCode: string;
};

export function ScoreCardHydrator({
  municipalityCode,
  ...slimProps
}: HydratorProps) {
  const [kentouItems, setKentouItems] = useState<ScoreCardProps["kentou_items"]>([]);
  const [sessions, setSessions] = useState<ScoreCardProps["sessions"]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchHeavyData = async () => {
      try {
        const res = await fetch(`/api/municipality/${municipalityCode}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setKentouItems(data.kentou_items ?? []);
        setSessions(data.sessions ?? []);
      } catch (err) {
        console.error("[ScoreCardHydrator] fetch failed:", err);
      } finally {
        setIsLoading(false);
      }
    };

    fetchHeavyData();
  }, [municipalityCode]);

  return (
    <ScoreCard
      {...slimProps}
      kentou_items={kentouItems}
      sessions={sessions}
      isLoadingHeavyData={isLoading}
    />
  );
}
```

30 行のコンポーネントです。`ScoreCard` 本体は一切変更していません。

## 6. Server Component 側の変更: stripHeavyArrays

ページコンポーネントから `ScoreCard` を呼ぶ部分を `ScoreCardHydrator` に差し替え、重い props を除去します。

```tsx
// src/app/[prefecture]/[city]/page.tsx (変更前)
import { ScoreCard } from "@/components/scorecard/ScoreCard";

export default async function CityPage({ params }: PageProps) {
  const data = await fetchMunicipalityData(params.city);

  return (
    <main>
      <ScoreCard
        municipalityCode={data.code}
        score={data.score}
        score_history={data.score_history}
        municipality_info={data.municipality_info}
        kentou_items={data.kentou_items}   // 723KB
        sessions={data.sessions}           // 196KB
      />
    </main>
  );
}
```

```tsx
// src/app/[prefecture]/[city]/page.tsx (変更後)
import { ScoreCardHydrator } from "@/components/scorecard/ScoreCardHydrator";

function stripHeavyArrays<T extends { kentou_items?: unknown; sessions?: unknown }>(
  data: T
): Omit<T, "kentou_items" | "sessions"> {
  const { kentou_items: _k, sessions: _s, ...rest } = data;
  return rest;
}

export default async function CityPage({ params }: PageProps) {
  const data = await fetchMunicipalityData(params.city);
  const slimData = stripHeavyArrays(data);

  return (
    <main>
      <ScoreCardHydrator
        municipalityCode={data.code}
        score={slimData.score}
        score_history={slimData.score_history}
        municipality_info={slimData.municipality_info}
      />
    </main>
  );
}
```

`stripHeavyArrays` は TypeScript のジェネリクスで型安全に配列を除去します。`Omit` を使うことで、呼び出し側が重い props を誤って渡せないよう型レベルで保護しています。

## 7. ハマったポイント: undefined ではなく空配列を渡す

最初の実装では `kentou_items={undefined}` と渡していました。これが prerender 時に TypeError を引き起こしました。

```
TypeError: Cannot read properties of undefined (reading 'filter')
```

`ScoreCard` 内部では `kentou_items.filter(...)` のような操作が随所にあります。1,586 行のコンポーネント全体を調査してオプショナルチェーン `?.filter()` を追加するのは現実的ではありません。

**解決策は空配列 `[]` を渡すことです。** 

```tsx
// ❌ prerender でクラッシュ
setKentouItems(undefined);

// ✅ 安全に初期描画できる
setKentouItems([]);
```

空配列であれば `.filter()` や `.map()` は「0 件のデータ」として動作し、エラーになりません。UI は「データなし」状態で初回描画し、fetch 完了後に全件表示へと切り替わります。

同様の理由で、API fetch が失敗した場合も `data.kentou_items ?? []` というフォールバックを入れています。ネットワークエラーでページがクラッシュすることを防げます。

## 8. API エンドポイントの実装

`/api/municipality/[code]` は重いデータのみを返す専用エンドポイントです。

```tsx
// src/app/api/municipality/[code]/route.ts
import { NextRequest, NextResponse } from "next/server";
import { fetchMunicipalityHeavyData } from "@/lib/data/municipality";

export async function GET(
  _req: NextRequest,
  { params }: { params: { code: string } }
) {
  try {
    const data = await fetchMunicipalityHeavyData(params.code);
    return NextResponse.json({
      kentou_items: data.kentou_items,
      sessions: data.sessions,
    });
  } catch (err) {
    console.error("[api/municipality] error:", err);
    return NextResponse.json(
      { error: "Failed to fetch municipality data" },
      { status: 500 }
    );
  }
}
```

このエンドポイントは `ScoreCardHydrator` 専用です。他のコンポーネントから呼ぶ場合は用途に合わせてフィールドを追加してください。

## 9. 結果: 2,006,236 bytes → 152,041 bytes

本番デプロイ後、同じ豊島区ページを再計測しました。

```python
import re

with open("toshima_after.html", "r", encoding="utf-8") as f:
    html = f.read()

rsc_chunks = re.findall(r'self\.__next_f\.push\(\[.*?\]\)', html, re.DOTALL)
total_rsc_size = sum(len(chunk.encode("utf-8")) for chunk in rsc_chunks)
total_size = len(html.encode("utf-8"))

print(f"HTML 総サイズ: {total_size:,} bytes")
print(f"RSC チャンク計: {total_rsc_size:,} bytes")
print(f"削減率: {(1 - total_size / 2_006_236) * 100:.1f}%")
```

```
HTML 総サイズ: 152,041 bytes
RSC チャンク計: 51,218 bytes
削減率: 92.4%
```

| 指標 | Before | After | 変化 |
|------|--------|-------|------|
| HTML サイズ | 2,006,236 bytes | 152,041 bytes | -92.4% |
| RSC チャンク | 1,905,924 bytes | 51,218 bytes | -97.3% |
| gzip 後 | 335 KB | 28 KB | -91.6% |
| LCP 推定 | 2〜3 秒 | 0.5 秒未満 | 大幅改善 |

**ScoreCard 本体（1,586 行）には一切触れていません。** 新規に書いたコードは `ScoreCardHydrator.tsx`（30 行）と `stripHeavyArrays`（8 行）の合計 38 行のみです。

## 10. UX の変化: progressive loading パターン

この wrapper pattern を使うと、ページの描画が 2 フェーズになります。

**フェーズ 1（初回描画、高速）**
HTML が届いた瞬間に `ScoreCard` が空の `kentou_items` と `sessions` でレンダリングされます。スコアや自治体基本情報は SSR 時に props として渡っているため、即座に表示されます。

**フェーズ 2（fetch 完了後）**
`useEffect` が発火し `/api/municipality/[code]` から重いデータを取得します。`setKentouItems` と `setSessions` で state が更新され、施策タブと会議記録タブが表示されます。

ユーザーにとっては「ページが一瞬で表示され、少し遅れて詳細データが出てくる」体験です。重いデータはスクロールしないと見えないタブの中にあるため、視覚的な違和感はほぼありません。

`isLoadingHeavyData` prop を使えばタブ内にスケルトンを表示することも容易です。

```tsx
// ScoreCard 側の対応例（最小変更）
function KentouTab({ kentou_items, isLoadingHeavyData }: TabProps) {
  if (isLoadingHeavyData) {
    return <SkeletonList count={5} />;
  }
  return <KentouItemList items={kentou_items} />;
}
```

## 11. このパターンが使えるケース

RSC prop slim wrapper pattern が有効な条件を整理します。

**条件 1: client component が大量の配列 props を受け取っている** 

`JSON.stringify(props).length` を計測してみてください。数百KB を超えているなら対象です。

**条件 2: 重いデータがあるタブや折りたたみの中に隠れている** 

初回描画に必要なデータと、インタラクション後に必要なデータを分離できる構造であれば効果的です。

**条件 3: 既存コンポーネントの書き換えが困難** 

レガシーコンポーネント、テスト網羅率が低い部分、外部パッケージから取得した大規模コンポーネントなど、リグレッションリスクが高い箇所に特に有効です。

**適用が難しいケース** 

初回描画から全データを必要とするコンポーネント（テーブル全件表示、PDF 出力など）には不向きです。また、fetch 回数が増えるため、同じページで複数の heavy component が存在すると API リクエスト数が増加します。その場合は 1 回の fetch でまとめて取得し、Context や props drilling で配布する設計を検討してください。

## 12. RSC payload のデバッグ方法まとめ

本番ページの RSC payload を手早く計測する Python ワンライナーです。

```python
# 1. HTML を保存
# curl -s https://example.com/heavy-page > page.html

# 2. RSC チャンクサイズを計測
python3 -c "
import re, sys
html = open('page.html').read()
chunks = re.findall(r'self\.__next_f\.push\(\[.*?\]\)', html, re.DOTALL)
total = sum(len(c.encode()) for c in chunks)
print(f'RSC: {total:,} bytes / Total: {len(html.encode()):,} bytes ({total/len(html.encode())*100:.1f}%)')
"
```

Chrome DevTools の場合は Network タブでページをリロードし、`Doc` フィルターで対象の HTML レスポンスを選択、`Response` タブで `__next_f` を検索すれば RSC チャンクを直接確認できます。

## まとめ

今回の実装をまとめます。

- **原因**: Server Component から client component に渡した `kentou_items`（723KB）と `sessions`（196KB）が HTML に丸ごとシリアライズされていた
- **解決策**: 30 行の `ScoreCardHydrator.tsx` を新設し、SSR 時は空配列、`useEffect` で非同期 fetch してマージ
- **成果**: 2,006,236 bytes → 152,041 bytes（92.4% 削減）、LCP 推定 2〜3 秒 → 0.5 秒未満
- **ポイント**: `undefined` ではなく空配列 `[]` を渡す。既存コンポーネント本体は無変更

Next.js の RSC は非常に便利ですが、「props に渡したデータは全て HTML に埋め込まれる」という事実を忘れると、今回のようなサイズ爆発が起きます。`curl -s URL | wc -c` で定期的にページサイズを確認する習慣をつけることを推奨します。

---

### 参考

- [Next.js 公式: Server and Client Components](https://nextjs.org/docs/app/building-your-application/rendering/server-components)
- [React: RSC payload の仕組み](https://react.dev/reference/rsc/server-components)
- [Web Vitals: LCP の最適化](https://web.dev/articles/lcp)

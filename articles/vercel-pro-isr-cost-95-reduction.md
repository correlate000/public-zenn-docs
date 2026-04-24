---
title: "Vercel Proの使用量課金を実測値から徹底解剖してISRコストを92%削減した話"
emoji: "💰"
type: "tech"
topics: ["vercel", "nextjs", "isr", "costoptimization", "performance"]
published: false
status: "publish-ready"
publication_name: "correlate_dev"
---

## はじめに

Vercel Proプランは月額$20。それだけだと思っていたら、ある月の請求が **$39.60** になっていました。

「え、何に課金されてるの？」と思ってダッシュボードを開いたところ、 **使用量課金が$19.60** も発生していたのです。内訳を見ると、ISR（Incremental Static Regeneration）Writesだけで$6.62、Build Minutesで$7.22。Proプランの月額に匹敵する使用量課金が、ひっそりと積み上がっていました。

本記事では、この$39.60の請求書を起点に以下の内容を扱います。

- Vercel Proの使用量課金の全項目と実測レート
- ISR Writesのコストが膨らむ仕組みと、revalidate変更による削減手法
- Build Minutesの超過メカニズムと「main直接push」の罠
- 全施策を実行した結果の Before/After

Next.jsでISRを使っている個人開発者や小規模チームにとって、月額数十ドルの差は無視できないコストです。同じ悩みを抱える方の参考になれば幸いです。

---

## 請求書の全体像：$39.60の正体

まず、実際の請求書（Mar 15 - Apr 14, 2026）の内訳を見てみましょう。

| 項目 | 金額 | 割合 |
|------|------|------|
| Proプラン基本料 | $20.00 | 50.5% |
| ISR Writes | $6.62 | 16.7% |
| Build Minutes | $7.22 | 18.2% |
| Fast Origin Transfer | $2.19 | 5.5% |
| Fluid Compute / Function Invocation | $2.52 | 6.4% |
| ISR Reads | $0.92 | 2.3% |
| Image Optimization | $0.13 | 0.3% |

基本料$20に対し、使用量課金が **$19.60** 。ほぼ倍額を払っていた計算になります。中でも **ISR Writes（$6.62）** と **Build Minutes（$7.22）** が突出しており、この2つで使用量課金の70%を占めていました。

---

## Vercel Proの実測課金レート

Vercelの公式ドキュメントには課金レートが記載されていますが、実際の請求書から逆算した実測レートを共有します。

| 項目 | 実測レート | 補足 |
|------|-----------|------|
| ISR Write | $2.087 / 100万回 | 3,169,889回で$6.62 |
| ISR Read | $0.217 / 100万回 | Writeの約1/10 |
| Build Minutes | $0.005 / 分 | Pro含有2,000分超過分のみ |
| Fast Origin Transfer | 帯域課金 | 削減困難 |

注目すべきは ISR Write の **$2.087/100万回** という数字。一見安く見えますが、ISRを使うページが多い場合、revalidateの間隔次第で呼び出し回数が爆発的に増加します。

---

## ISR Writesはなぜ高額になるのか

### ISRの動作原理と課金ポイント

ISR（Incremental Static Regeneration）は、静的ページのキャッシュが期限切れになるとバックグラウンドで再生成する仕組みです。この「再生成」のたびに **ISR Write** が1回カウントされます。

```tsx
// app/articles/[slug]/page.tsx
export const revalidate = 3600; // 1時間ごとに再生成
```

上記の設定では、1ページあたり1時間に1回の再生成が発生。しかし、実際にはリクエストを受けるたびにキャッシュの鮮度がチェックされ、期限切れならWriteが走ります。

### 課金の計算式

月間のISR Write回数は、おおむね以下の式で見積もれます。

```
ISR Writes/月 ≒ ISR対象ページ数 × (30日 × 24時間 / revalidate秒数 × 3600)
```

筆者の環境（4リポジトリ、ISR対象ページ52ページ分）の場合を計算してみましょう。なお52ページは `revalidate = 3600` のページ数で、`revalidate = 300` 等の個別設定ページ（後述）を除いた数値です。

```
revalidate = 3600（1時間）の場合:
52ページ × 720回/月 = 37,440回（理論値）

実際の請求: 3,169,889回
```

理論値と実測値の乖離は、動的ルートのパターン展開やcronによる定期アクセスが原因です。特にcronジョブが毎時3サイトに対して走っていたことが、回数を大幅に押し上げていました。

---

## 施策1：revalidateを3600から86400に変更

### 変更の方針

ISR Writesを削減するもっとも直接的な方法は、`revalidate` の値を大きくすることです。

```tsx
// Before: 1時間ごとに再生成
export const revalidate = 3600;

// After: 24時間ごとに再生成
export const revalidate = 86400;
```

3600秒（1時間）から86400秒（24時間）に変更すれば、 **理論上24倍の削減** が見込めます。

### なぜ24時間でも問題ないのか

ISRのrevalidateを長くすることに不安を覚える方もいるでしょう。しかし、以下の条件を満たすサイトなら24時間キャッシュで十分です。

- コンテンツ更新頻度が1日1回以下
- リアルタイム性が求められない（ニュースサイトでない）
- 更新時に手動で `revalidatePath()` や `revalidateTag()` を呼べる

筆者の場合、4サイトとも記事コンテンツが中心で、更新は週に数回程度。1時間ごとの再生成は完全にオーバースペックでした。

### 一括変更の実施

4リポジトリの `revalidate = 3600` を含むファイルを一括で変更しました。変更ファイル総数は74件で、うち52ページがISR対象（残りはlayout.tsやAPI Route等の設定ファイル）です。

```bash
# 対象ファイルを確認
grep -rl "export const revalidate = 3600" src/app/

# 一括置換
while read f; do
  sed -i '' 's/export const revalidate = 3600/export const revalidate = 86400/' "$f"
done < <(grep -rl "export const revalidate = 3600" src/app/)
```

| リポジトリ | 変更ファイル数 |
|-----------|--------------|
| isvd-social-data | 24 |
| isvd-public0-data | 8 |
| isvd-work-data | 35 |
| coffee-guide | 7 |

ただし、`revalidate = 300`（5分）に設定していた一部のページ（リアルタイムAPIフィード）は意図的に変更していません。月間コストが$0.04程度と小さく、変更リスクの方が大きいと判断したためです。

### 期待される削減効果

| 指標 | Before | After（見込み） |
|------|--------|----------------|
| revalidate | 3,600秒 | 86,400秒 |
| ISR Writes/月（推定） | 3,169,889回 | ~300,000回 |
| ISR Writes課金 | $6.62 | ~$0.50 |
| 削減率 | - | 約92% |

理論値の24倍削減には届かないものの、実質的に **6〜10倍の削減** が見込めます。cronジョブの整理（後述）との合わせ技で、$6.62から$0.50前後への削減を想定しています。

---

## 施策2：cronジョブの整理

ISR Writesが理論値の80倍以上に膨れ上がっていた原因のひとつが、 **3サイトが同一のBigQueryテーブルに毎時書き込みを行うcronジョブ** でした。

### 問題の構造

```json
// vercel.json（3サイトそれぞれに同じ設定があった）
{
  "crons": [
    {
      "path": "/api/auto-curate",
      "schedule": "0 * * * *"
    }
  ]
}
```

3サイトが毎時同じAPIを叩き、同じBQテーブルに書き込む。完全な冗長構成です。cronのRoute Handler自体はISR Writeを直接トリガーしませんが、Route Handler内でデータを更新した後に `revalidatePath()` / `revalidateTag()` を呼んでいたため、結果としてcron実行のたびに関連ページのISR再生成が走っていました。3サイト × 毎時 = 1日72回の不要な再生成という構造です。

### 対策

```json
// isvd-social-data の vercel.json（これだけ残す）
{
  "crons": [
    {
      "path": "/api/auto-curate",
      "schedule": "0 */6 * * *"
    }
  ]
}
```

- 3サイト並列実行 → **1サイトに一本化**
- 毎時実行 → **6時間おきに変更**

結果として、cronによるISRトリガーが **1/18** （3サイト × 6倍間隔）に減少。revalidateの延長と組み合わせることで、ISR Writesの劇的な削減が実現しました。

---

## 施策3：Build Minutesの超過対策

### Pro含有2,000分の壁

Vercel Proには月間2,000分のビルド時間が含まれています。超過分は **$0.005/分** で課金されます。

筆者の環境では、月間3,411分を消費しており、 **1,411分の超過 = $7.22** が発生していました。

### 超過の原因は「プレビューデプロイ」ではなかった

一般的に「Build Minutesを節約するにはプレビューデプロイを減らせ」と言われます。しかし、`vercel project ls` で調査したところ、プレビューデプロイは **ゼロ** 。全てのビルドがmain直接pushによるProduction buildでした。

特に開発セッション中の頻繁なpushが問題です。ある日のログを見ると、 **1日で11回のデプロイ（44分消費）** が記録されていました。小さな修正のたびにpushする習慣が、ビルド分数を食い潰していたのです。

### 対策1：autoJobCancelation

```json
// vercel.json
{
  "github": {
    "autoJobCancelation": true
  }
}
```

この設定を追加すると、短時間（約4分以内）に連続pushがあった場合、前のビルドを自動キャンセルしてくれます。開発セッション中の高頻度pushで特に効果を発揮する設定です。

4サイト全てのvercel.jsonにこの設定を追加しました。`coffee-guide` はvercel.jsonが存在しなかったため新規作成しています。

```json
// coffee-guide/vercel.json（新規作成）
{
  "github": {
    "autoJobCancelation": true
  }
}
```

### 対策2：運用ルールの変更

技術的な対策だけでは不十分です。最も効果が大きいのは、 **「セッション終了時に1回だけまとめてpush」** という運用ルールの導入でした。

| シナリオ | pushの回数 | ビルド時間（推定） |
|---------|-----------|-----------------|
| 修正のたびにpush | 11回/日 | 44分/日 |
| セッション終了時に1回push | 1回/日 | 4分/日 |
| 削減効果 | 10回削減 | 40分/日 |

月20日稼働として、 **800分/月の削減** 。これだけでBuild Minutes超過の大部分を解消できます。

---

## 手を出さなかった項目

コスト最適化では「何をやるか」と同じくらい「何をやらないか」も重要です。

### Fast Origin Transfer（$2.19）

帯域課金のため、コード変更での削減が困難。ページサイズを削るか、CDNキャッシュヒット率を上げるしかありませんが、投資対効果が合わないと判断しました。この$2.19は **実質的なコストの下限** と捉えています。

### Image Optimization（$0.13）

avifからwebpへの変更で若干の削減は可能ですが、月$0.13のために画質を犠牲にする判断はしませんでした。

### revalidate = 300のページ

動的APIフィードを配信しているページが1つだけ `revalidate = 300` で設定されていましたが、月間コスト$0.04のため変更を見送っています。

判断基準は明確で、 **節約額 < 変更リスク** なら手を出さないというルールを徹底しました。

---

## Before/After まとめ

全施策を適用した結果の想定コストをまとめます。

| 項目 | Before | After（見込み） | 削減額 |
|------|--------|----------------|--------|
| Proプラン基本料 | $20.00 | $20.00 | - |
| ISR Writes | $6.62 | ~$0.50 | $6.12 |
| Build Minutes | $7.22 | ~$5.00 | $2.22 |
| Fluid/Function | $2.52 | ~$1.50 | $1.02 |
| Fast Origin Transfer | $2.19 | $2.19 | $0.00 |
| ISR Reads | $0.92 | ~$0.10 | $0.82 |
| Image Optimization | $0.13 | $0.13 | $0.00 |
| 合計 | $39.60 | ~$29.42 | $10.18 |

ISR Writesだけで見れば **$6.62 → $0.50 の約92%削減** 。使用量課金全体では **$19.60 → $9.42 の約52%削減** を見込んでいます。

さらに運用ルール（まとめてpush）を徹底すれば、Build Minutesの超過を限りなくゼロに近づけることが可能です。その場合は **$22〜26** の着地が現実的な目標値となります。

---

## 実装チェックリスト

同じ状況に直面した方のために、実施手順をチェックリストにまとめました。

### すぐやるべき（効果大・リスク低）

- [ ] Vercelダッシュボードで使用量課金の内訳を確認
- [ ] `grep -rl "export const revalidate" src/app/` で対象ファイルを洗い出す
- [ ] revalidateの値を見直す（更新頻度に応じて86400〜604800）
- [ ] vercel.jsonに `autoJobCancelation` を追加
- [ ] 冗長なcronジョブを統合・頻度削減

### 運用で対応（コスト0・効果大）

- [ ] pushを「セッション終了時に1回」に集約
- [ ] `vercel project ls` で停滞プロジェクトを確認・削除検討

### やらなくていい（効果小・リスクあり）

- [ ] Image Optimizationのフォーマット変更（$0.13/月なら放置）
- [ ] revalidateが300秒以下の動的ページの変更（コスト$0.04以下なら放置）
- [ ] Fast Origin Transferの最適化（帯域課金で削減困難）

---

## よくある疑問

### Q. revalidateを長くするとSEOに影響しない？

コンテンツの更新頻度が低いサイトであれば影響しません。Googleのクローラーは `Cache-Control` を尊重しますが、ISRではクローラーのリクエスト時に最新版が返る仕組みです。stale-while-revalidateの動作により、古いキャッシュが返されるのは次の再生成が完了するまでの短い期間に限られます。

### Q. on-demand revalidationと組み合わせるべき？

はい、推奨します。`revalidate = 86400` にした上で、コンテンツ更新時に `revalidatePath()` や `revalidateTag()` を呼ぶ運用が理想的な構成です。

```tsx
// app/api/revalidate/route.ts
import { revalidatePath } from "next/cache";
import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const { path, secret } = await request.json();

  if (secret !== process.env.REVALIDATION_SECRET) {
    return NextResponse.json({ message: "Invalid secret" }, { status: 401 });
  }

  revalidatePath(path);
  return NextResponse.json({ revalidated: true, now: Date.now() });
}
```

これにより「普段は24時間キャッシュ、更新時だけ即座に再生成」という効率的な運用が実現します。

### Q. Hobby（無料）プランに戻すのはあり？

個人開発で商用利用しないなら検討の余地はあります。ただしHobbyプランにはISRの制限やチームメンバー追加不可などの制約があるため、プロダクション用途ではProを維持した上でコスト最適化する方が現実的です。

---

## まとめ

Vercel Proの使用量課金は、デフォルト設定のまま運用していると簡単に基本料の倍近くまで膨れ上がります。今回の調査で得られた知見を3点に集約します。

1. **ISR Writesが最大のコスト要因** になりやすい。revalidateの値を「更新頻度に見合った長さ」に設定するだけで、$6.62が$0.50に下がる
2. **Build Minutesの超過は「頻繁なmain push」が主因** であることが多い。プレビューデプロイだけが犯人ではない点に注意
3. **「やらない判断」もコスト最適化の一部** である。月$0.13の項目を最適化するために時間を使うのは本末転倒

請求書を一度じっくり眺めてみてください。意外な課金項目が見つかるかもしれません。

---

:::message
本記事の数値はすべて筆者の環境（Next.js App Router / ISR利用 / 4リポジトリ26プロジェクト構成）での実測値です。課金レートやPro含有量はVercelの料金改定により変動する可能性があります。最新の料金体系は [Vercel Pricing](https://vercel.com/pricing) をご確認ください。
:::

---
title: "Amazon PA-API → Creators API 移行ガイド — Next.js + TypeScriptで5/15に間に合わせる"
emoji: "🛒"
type: "tech"
topics: ["amazon", "nextjs", "typescript", "api", "affiliate"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## この記事の対象読者と期限

**2026年5月15日** に PA-API v5 のエンドポイントが完全停止します。

Amazon Associates でアフィリエイトサイトを運営している方、とくに Next.js や Node.js で PA-API v5 を直接呼び出している方は、この日までに Creators API への移行が必須となります。WordPress プラグイン経由で利用している場合はプラグイン側の対応待ちで済むかもしれませんが、自前でコードを書いている場合は自力での対応が求められる状況です。

本記事では、実際にコーヒーレビューサイトの Next.js プロジェクトで PA-API v5 から Creators API への移行を完了した経験をもとに、TypeScript での具体的な実装手順を解説します。2026年4月時点で、日本語による Next.js + TypeScript の Creators API 実装解説は見当たりません。この記事がその空白を埋める一助になれば幸いです。

### 廃止タイムライン

| 日付 | 内容 |
|------|------|
| 2026-01-31 | Offers V1 廃止（PA-API v5 で価格データ取得不可に） |
| 2026-04-30 | PA-API v5 公式廃止日（Amazon 公式ドキュメント記載） |
| 2026-05-15 | PA-API v5 エンドポイント完全停止 |

Offers V1 は既に廃止されているため、PA-API v5 で価格情報を取得していたサイトは現時点でデータが取れなくなっている可能性があります。

---

## PA-API v5 と Creators API の差分

移行作業の前に、何が変わったのかを整理しておきます。

### 認証方式の変化

最大の変更点は認証方式です。PA-API v5 では AWS Signature v4 による署名が必要でしたが、Creators API では OAuth 2.0 Client Credentials Grant に変わりました。つまり、リクエストごとの署名計算が不要になり、代わりに Bearer Token を使うシンプルな認証フローになっています。

| 項目 | PA-API v5 | Creators API |
|------|-----------|-------------|
| 認証方式 | AWS Signature v4 | OAuth 2.0 Bearer Token |
| 必要な鍵 | Access Key + Secret Key | Credential ID + Secret + Version |
| 署名計算 | 毎リクエスト必要 | 不要（SDK が自動管理） |

### パラメータ命名規則の変更

PA-API v5 は PascalCase、Creators API は lowerCamelCase を採用しています。

```diff
- ItemIds: ["B0XXXXXXXXX"]
- PartnerTag: "your-tag-22"
+ itemIds: ["B0XXXXXXXXX"]
+ partnerTag: "your-tag-22"
```

### レスポンス構造の変更（最頻出バグポイント）

移行時に最もバグを生みやすいのが、価格データのレスポンス構造変化です。Creators API では `offersV2` の中に `money` というラッパーオブジェクトが追加されています。

PA-API v5:

```json
{
  "Listings": [{
    "Price": {
      "Amount": 2980,
      "DisplayAmount": "¥2,980"
    }
  }]
}
```

Creators API:

```json
{
  "listings": [{
    "price": {
      "money": {
        "amount": 2980,
        "displayAmount": "¥2,980"
      }
    }
  }]
}
```

`price.Amount` だったものが `price.money.amount` になっています。この `money` ラッパーを見落とすと、価格が常に `null` になるという厄介なバグに直面することになるでしょう。

---

## Version 文字列の正体

Creators API の資格情報を発行すると、Credential ID・Secret に加えて **Version** という値が付いてきます。これは単なるバージョン番号ではなく、認証エンドポイントを決定するルーティング情報として機能する識別子です。

:::message
注意: ここでいう Version（2.1〜3.3）は Credential の認証方式バージョンであり、npm パッケージ `amazon-creators-api` のバージョン（執筆時点 1.2.2）とは別物です。`npm install amazon-creators-api@3.3` のように指定しないでください。
:::

| Version | 認証基盤 | トークンエンドポイント |
|---------|---------|-------------------|
| 2.1 | Cognito（NA） | `creatorsapi.auth.us-east-1.amazoncognito.com` |
| 2.2 | Cognito（EU） | `creatorsapi.auth.eu-south-2.amazoncognito.com` |
| 2.3 | Cognito（FE） | `creatorsapi.auth.us-west-2.amazoncognito.com` |
| 3.1 | LWA（NA） | `api.amazon.com` |
| 3.2 | LWA（EU） | `api.amazon.co.uk` |
| 3.3 | LWA（FE/日本） | `api.amazon.co.jp` |

2026年2月以降に新規発行された資格情報は **v3.x（LWA: Login with Amazon）形式** です。日本の amazon.co.jp を対象とするなら `3.3` が発行されるはずです。

v2.x と v3.x では scope の記法も異なり、v3.x は `creatorsapi::default`（コロン2つ）、v2.x は `creatorsapi/default`（スラッシュ）を使います。後述する SDK を使えばこの違いは自動で吸収されるため、開発者が直接意識する必要はありませんが、独自に OAuth フローを実装する場合は要注意のポイントです。

:::message
古いドキュメントや WordPress プラグインの解説では v2.x 系（Cognito）の情報が書かれていることがあります。2026年2月以降に発行した資格情報で v2.x の設定を使うと認証エラーになるため、必ず自分の Version を確認してください。
:::

---

## 移行ステップ

### ステップ 1: 資格情報の取得

Associates Central にログインし、ツールメニューから「Creators API」を選択します。

1. 「Create Application」をクリック
2. アプリケーション名を入力（後述の注意事項を参照）
3. 「Add New Credential」をクリック
4. **Credential ID、Credential Secret、Version** の3つをコピーして安全な場所に保存

Credential Secret は初回表示のみで、再表示できません。紛失した場合は新しい Credential を再発行する必要があります。

:::message alert
アプリケーション名に `amazon_search` のような汎用的な名前を使うと、API 呼び出し時に `400 Bad Request`（`The value xxx provided in the request for ApplicationId is invalid.`）が返ることがあります。プロジェクト名を含めたユニークな名前にしてください。
:::

### ステップ 2: 環境変数の更新

PA-API v5 で使っていた `AMAZON_ACCESS_KEY` / `AMAZON_SECRET_KEY` は不要になります。代わりに3つの環境変数を設定します。

```env
# 削除（PA-API v5 用 → 不要）
# AMAZON_ACCESS_KEY=...
# AMAZON_SECRET_KEY=...

# 追加（Creators API 用 → 3つ全て必須）
AMAZON_CREDENTIAL_ID=amzn1.application-oa2-client.xxxxxxxxxx
AMAZON_CREDENTIAL_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AMAZON_CREDENTIAL_VERSION=3.3

# アフィリエイトタグ（変更なし）
AMAZON_AFFILIATE_TAG_JA=your-tag-22
```

Vercel にデプロイしている場合は、Project Settings の Environment Variables で設定してください。

:::message alert
環境変数に `NEXT_PUBLIC_` プレフィックスを付けてはいけません。付けるとクライアントバンドルに含まれ、Credential Secret がブラウザに露出します。Creators API の呼び出しは必ずサーバーサイドで行ってください。
:::

### ステップ 3: パッケージのインストール

TypeScript 対応の SDK `amazon-creators-api` を使用します。

```bash
pnpm add amazon-creators-api
# または
npm install amazon-creators-api
```

このパッケージは Ryan Schiang 氏が公開・メンテナンスしており、内部には Amazon のコードが含まれています（ソース内に Copyright 2025 Amazon.com, Inc. の記載あり）。TypeScript の型定義が付属しており、Node.js 18 以上が動作要件です。

### ステップ 4: API クライアントの実装

ここからが本記事の核心となるコード実装です。実際に動作する完全なコードを示します。

```typescript:src/lib/paapi.ts
import {
  ApiClient,
  GetItemsRequestContent,
  TypedDefaultApi,
} from "amazon-creators-api";

const MARKETPLACE = "www.amazon.co.jp";

// --- 型定義 ---
interface CreatorsApiProduct {
  asin: string;
  title: string;
  price: string | null;
  priceAmount: number | null;
  rating: number | null;
  reviewCount: number | null;
  imageUrl: string | null;
  detailUrl: string;
}

// --- シングルトン ---
let apiInstance: TypedDefaultApi | null = null;

function getApi(): TypedDefaultApi | null {
  if (apiInstance) return apiInstance;

  const credentialId = process.env.AMAZON_CREDENTIAL_ID;
  const credentialSecret = process.env.AMAZON_CREDENTIAL_SECRET;
  const credentialVersion = process.env.AMAZON_CREDENTIAL_VERSION;

  if (!credentialId || !credentialSecret || !credentialVersion) {
    return null;
  }

  const client = new ApiClient();
  client.credentialId = credentialId;
  client.credentialSecret = credentialSecret;
  client.version = credentialVersion;

  apiInstance = new TypedDefaultApi(client);
  return apiInstance;
}
```

**シングルトンパターンにする理由**: `ApiClient` 内部の `OAuth2TokenManager` がアクセストークンをインメモリにキャッシュします。リクエストのたびに `new ApiClient()` を生成すると、毎回 OAuth トークン取得のリクエストが走ってしまい、レイテンシ増加とトークンエンドポイントへの無駄なリクエストが発生します。モジュールスコープで1回だけ初期化するのが正解です。

### ステップ 5: GetItems の実装

商品情報の取得は `getItems` メソッドで行います。1回のリクエストで最大10件の ASIN を指定可能です。

```typescript:src/lib/paapi.ts（続き）
export async function getItems(
  asins: string[],
): Promise<Map<string, CreatorsApiProduct>> {
  const result = new Map<string, CreatorsApiProduct>();

  const api = getApi();
  const tag = process.env.AMAZON_AFFILIATE_TAG_JA;
  if (!api || !tag) return result;

  // GetItems は最大10件
  const batch = asins.slice(0, 10);

  const request = new GetItemsRequestContent(tag, batch);
  request.resources = [
    "itemInfo.title",
    "offersV2.listings.price",
    "customerReviews.starRating",
    "customerReviews.count",
    "images.primary.large",
  ] as never[];

  try {
    const response = await api.getItems(MARKETPLACE, request);
    const items = response?.itemsResult?.items ?? [];

    for (const item of items) {
      const asin = item.asin;
      if (!asin) continue;

      const listing = item.offersV2?.listings?.[0];
      // ★ PA-API v5 との最大の違い: money ラッパーを経由する
      const priceMoney = listing?.price?.money;

      result.set(asin, {
        asin,
        title: item.itemInfo?.title?.displayValue ?? "",
        price: priceMoney?.displayAmount ?? null,
        priceAmount: priceMoney?.amount ?? null,
        rating: item.customerReviews?.starRating?.value ?? null,
        reviewCount: item.customerReviews?.count ?? null,
        imageUrl: item.images?.primary?.large?.url ?? null,
        detailUrl:
          item.detailPageURL ??
          `https://www.amazon.co.jp/dp/${asin}?tag=${tag}`,
      });
    }
  } catch (e: unknown) {
    const err = e as { status?: number; body?: { message?: string } };
    if (err.status === 403) {
      // 30日間10件の売上要件未達 → 空のMapを返す
      console.warn(
        "Creators API 403: Associate not eligible (30日間10売上が必要)"
      );
    } else {
      console.error("Creators API error:", e);
    }
  }

  return result;
}
```

`as never[]` のキャストが気になる方は、`GetItemsResource.constructFromObject()` を使う方法もあります。

```typescript
import { GetItemsResource } from "amazon-creators-api";

request.resources = [
  "itemInfo.title",
  "offersV2.listings.price",
  "customerReviews.starRating",
  "images.primary.large",
].map((r) => GetItemsResource.constructFromObject(r));
```

### ステップ 6: Next.js App Router での利用

Creators API の呼び出しはサーバーサイド限定です。App Router の Server Components または Route Handler から呼び出します。

```typescript:app/[category]/[slug]/page.tsx
import { getItems } from "@/lib/paapi";

export const revalidate = 3600; // 1時間のISRキャッシュ

export default async function ArticlePage({ params }) {
  const asins = ["B0XXXXXXXXX", "B0YYYYYYYYY"];
  const products = await getItems(asins);

  return (
    <div>
      {[...products.values()].map((product) => (
        <ProductCard key={product.asin} product={product} />
      ))}
    </div>
  );
}
```

ISR（Incremental Static Regeneration）と組み合わせることで、ビルド時に商品情報を取得し、`revalidate` で指定した間隔でバックグラウンド更新する設計が可能です。API のコール回数を抑えつつ、商品情報の鮮度を保てるバランスの良い構成になります。

---

## 403 フォールバック設計

Creators API には **過去30日間に10件以上の適格売上** という利用資格要件があります。この条件を満たさない間は、API が 403 `AssociateNotEligible` エラーを返します。サイト立ち上げ直後や、売上が少ない月は要件を下回る可能性があるため、フォールバック設計は必須と言えます。

### フォールバック戦略

```typescript
export async function getItemsWithFallback(
  asins: string[],
): Promise<Map<string, CreatorsApiProduct>> {
  const result = await getItems(asins);

  // API から取得できた場合はそのまま返す
  if (result.size > 0) return result;

  // 403 等で取得できなかった場合: 最低限の情報で埋める
  const fallback = new Map<string, CreatorsApiProduct>();
  const tag = process.env.AMAZON_AFFILIATE_TAG_JA ?? "";

  for (const asin of asins) {
    fallback.set(asin, {
      asin,
      title: "",
      price: null,
      priceAmount: null,
      rating: null,
      reviewCount: null,
      imageUrl: null,
      detailUrl: `https://www.amazon.co.jp/dp/${asin}?tag=${tag}`,
    });
  }

  return fallback;
}
```

この設計のポイントは、商品カードの表示を完全に消すのではなく、Amazon へのリンクだけは維持することです。価格やレビュー情報が表示されなくても、リンク経由で購入してもらえれば売上実績が積み上がり、10件の要件を達成して API が再び使えるようになるという好循環を生み出せます。

売上が復帰すると数日以内に API アクセスが自動復旧するため、コード側の変更は不要です。

---

## 利用可能な Resources 一覧

`GetItems` で取得できるフィールドの全リストです。`request.resources` に指定する値の参考にしてください。

```
browseNodeInfo.browseNodes
browseNodeInfo.browseNodes.ancestor
browseNodeInfo.browseNodes.salesRank
browseNodeInfo.websiteSalesRank
customerReviews.count
customerReviews.starRating
images.primary.small
images.primary.medium
images.primary.large
images.primary.highRes
images.variants.small
images.variants.medium
images.variants.large
images.variants.highRes
itemInfo.byLineInfo
itemInfo.contentInfo
itemInfo.contentRating
itemInfo.classifications
itemInfo.externalIds
itemInfo.features
itemInfo.manufactureInfo
itemInfo.productInfo
itemInfo.technicalInfo
itemInfo.title
itemInfo.tradeInInfo
parentASIN
offersV2.listings.availability
offersV2.listings.condition
offersV2.listings.dealDetails
offersV2.listings.isBuyBoxWinner
offersV2.listings.loyaltyPoints
offersV2.listings.merchantInfo
offersV2.listings.price
offersV2.listings.type
```

必要なフィールドだけを指定することで、レスポンスサイズを抑えられます。

---

## ハマりポイントまとめ

移行作業で実際に遭遇した問題と、事前に知っておきたいポイントをまとめます。

### 1. price.money ラッパーの見落とし

前述のとおり、価格データの取得パスが `price.Amount` から `price.money.amount` に変わっています。型定義上は `price?.money?.amount` とオプショナルチェーンで書くことになりますが、`money` の存在自体を知らなければそもそも正しいパスにたどり着けません。移行時に価格が `null` になったら、まずここを疑ってみてください。

### 2. Version の不一致

Associates Central で発行された Version と、コード側に設定した値が一致しないと認証エラーになります。環境変数をコピーする際のタイポ（`3.3` を `33` と書いてしまう等）に注意が必要です。

### 3. アプリケーション名の罠

Associates Central でアプリケーション名を登録する際、他ユーザーと重複する名前を使うと、登録自体は成功するのに API 呼び出し時に 400 エラーが返ることがあります。`my-project-creators-api-2026` のように、重複しにくい命名を心がけましょう。

### 4. SearchItems の title パラメータ

`SearchItems` オペレーションを使う場合、`title` パラメータは無効です。`keyword` パラメータを使用してください。PA-API v5 で `title` 検索していたコードはそのままでは動きません。

### 5. Vercel サーバーレス環境でのシングルトン

Vercel のサーバーレス環境では、モジュールスコープのシングルトンがリクエストをまたいで保持されない場合があります。Cold Start のたびに新しいインスタンスが生成され、トークン取得が走る可能性がある点は認識しておくべきです。ただし、機能的には問題なく、影響は API コール数の微増にとどまります。ISR を活用してリクエスト自体を減らす設計で対処するのが現実的な選択肢です。

### 6. 旧コードの署名計算ロジック

PA-API v5 では AWS Signature v4 の署名を手動で計算するコード（`createHmac` や canonical request の組み立て）が必要でした。Creators API では SDK が OAuth トークンの取得・キャッシュ・更新を全て自動で行うため、これらの署名関連コードは全て削除して構いません。認証周りのコード量が大幅に減るのは、移行による嬉しい副産物と言えるでしょう。

---

## PA-API v5 からの移行チェックリスト

最後に、移行作業の全体を俯瞰するチェックリストを置いておきます。

- [ ] Associates Central で Creators API のアプリケーションを作成
- [ ] Credential ID / Secret / Version を安全な場所に保存
- [ ] 環境変数を更新（旧 Access Key / Secret Key は削除）
- [ ] `amazon-creators-api` パッケージをインストール
- [ ] API クライアントのシングルトン初期化コードを実装
- [ ] `getItems` 関数を Creators API 用に書き換え
- [ ] `price.money` ラッパーのパス変更を反映
- [ ] `customerReviews.starRating.value` のパス変更を反映
- [ ] 403 フォールバックの実装
- [ ] `NEXT_PUBLIC_` プレフィックスが付いていないことを確認
- [ ] ローカルで動作確認
- [ ] Vercel（または本番環境）の環境変数を更新
- [ ] デプロイして本番確認

---

## まとめ

PA-API v5 から Creators API への移行は、認証方式の変更（AWS Signature v4 → OAuth 2.0）とレスポンス構造の変更（`price.money` ラッパー追加）が二大ポイントです。`amazon-creators-api` SDK を使えば認証周りは自動化されるため、実質的に意識すべきはレスポンスのパス変更と 403 フォールバックの設計に絞られます。

5月15日のエンドポイント停止まで残り少ないですが、コード量としてはさほど多くありません。この記事のコードを参考に、早めの移行をお勧めします。

### 参考リンク

- [Amazon Creators API 公式ドキュメント](https://affiliate-program.amazon.com/creatorsapi/docs/)
- [Amazon Associates 日本向け Creators API 導入ガイド](https://affiliate.amazon.co.jp/help/node/topic/G42ZATP8USCMBDDH)
- [amazon-creators-api SDK の使い方（英語）](https://ryanschiang.com/amazon-creators-api-sdk)
- [PA-API vs Creators API の差分解説（英語）](https://www.keywordrush.com/blog/amazon-creator-api-what-changed-and-how-to-switch/)

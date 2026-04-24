---
title: "ローンチ3週間のメディアをGA4 + Ad Grantsで立ち上げる全手順"
emoji: "📈"
type: "tech"
topics: ["ga4", "googleads", "adgrants", "nextjs", "seo"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

新しいメディアサイトを立ち上げた直後、「記事は100本以上あるのに月間ユーザーが19人しかいない」という状況に直面しました。これはフィクションではなく、2026年4月に実際に経験した数値です。

この記事では、公共資産活用をテーマにしたメディアサイト（Next.js + MDX 構成）のローンチ3週間後に実施した11施策を、実データとコード付きで解説します。GA4 Consent Mode v2の実装から Ad Grants PAUSED キーワードの復帰まで、再現可能な手順書として整理しました。

---

## 状況の把握：ローンチ3週間後のリアルな数値

まず、施策を打つ前の現状を正確に把握することから始めました。GA4とAd Grantsのデータを取得した結果が以下のとおりです。

### GA4 トラフィックデータ（4月1日〜22日）

| 指標 | 値 |
|------|-----|
| アクティブユーザー | 19 |
| セッション | 54 |
| PV | 87 |
| 新規ユーザー | 15 |
| エンゲージセッション | 29 |
| 平均セッション時間 | 500秒（約8分20秒） |
| 直帰率 | 46.3% |

ユーザー数は少ないものの、 **平均セッション時間が500秒** というのは注目すべき数値です。コンテンツ自体への関心はある。届いていないだけという仮説を立てました。

### チャネル別の内訳

| チャネル | セッション | エンゲージ率 |
|----------|-----------|------------|
| Direct | 39 | 48.7% |
| Paid Search | 6 | 83.3% |
| Organic Search | 5 | 60.0% |
| Referral | 3 | 33.3% |

Paid Search（Ad Grants経由）のエンゲージ率が **83.3%** と最も高い。広告から来たユーザーの質が良いにもかかわらず、セッション数がわずか6件というのは明らかに設定の問題です。

### Ad Grants の状況（30日間）

- PPP PFI 関連キーワード: QS=8、インプレッション 1,427
- 予算消化率: **1.5%**（月間 $10,000 の枠のうち $150 しか使っていない）
- PAUSED 状態のキーワード: 20件

予算の98.5%が未使用でした。稼働しているように見えて、ほぼ止まっていた状態です。

---

## 施策の全体像：MECE で4領域に分類

現状を把握したうえで、施策を「集客→回遊→転換→継続」の4領域に整理しました。

| 領域 | 施策 | 期待効果 |
|------|------|---------|
| 集客 | Ad Grants PAUSED KW 復帰 | 予算消化率1.5%→15-30% |
| 集客 | SEOタイトル最適化 | CTR改善 |
| 集客 | FAQ schema 自動生成 | リッチリザルト獲得 |
| 回遊 | 内部リンク孤立記事解消 | 孤立記事49→0 |
| 転換 | GA4 キーイベント登録 | コンバージョン計測精度向上 |
| 転換 | NL登録 CTA 追加 | マイクロCVの増加 |
| 継続 | GA4 Consent Mode v2 | プライバシー準拠＋計測精度維持 |
| 継続 | SNSパイプライン修正 | 記事公開→自動投稿の正常化 |

以下、実装の詳細をコード付きで解説します。

---

## 1. GA4 Consent Mode v2 の実装

最初に対応したのが Consent Mode v2 です。GDPR・日本の個人情報保護法への対応だけでなく、Cookie 未承認ユーザーの行動をモデリングで補完できる点が重要です。

### 変更箇所

`ga4-scripts.tsx` の gtag 設定に `url_passthrough` と `ads_data_redaction` を追加します。

```tsx
// ga4-scripts.tsx
'use client'

import Script from 'next/script'

const GA_MEASUREMENT_ID = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID

export function GA4Scripts() {
  return (
    <>
      <Script
        src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
        strategy="afterInteractive"
      />
      <Script id="ga4-init" strategy="afterInteractive">
        {`
          window.dataLayer = window.dataLayer || [];
          function gtag(){dataLayer.push(arguments);}

          // Consent Mode v2: デフォルトは全拒否
          gtag('consent', 'default', {
            'analytics_storage': 'denied',
            'ad_storage': 'denied',
            'ad_user_data': 'denied',
            'ad_personalization': 'denied',
            'wait_for_update': 500
          });

          gtag('js', new Date());
          gtag('config', '${GA_MEASUREMENT_ID}', {
            url_passthrough: true,
            ads_data_redaction: true
          });
        `}
      </Script>
    </>
  )
}
```

`cookie-consent-banner.tsx` 側では、ユーザーが承認した際に `gtag('consent', 'update', ...)` を呼び出します。

```tsx
// cookie-consent-banner.tsx（承認時の処理）
function handleAccept() {
  gtag('consent', 'update', {
    analytics_storage: 'granted',
    ad_storage: 'granted',
    ad_user_data: 'granted',
    ad_personalization: 'granted',
  })
  localStorage.setItem('cookie-consent', 'granted')
  setVisible(false)
}
```

`url_passthrough: true` のポイントは、Cookie 未承認でも GCLID 情報を URL パラメータ経由で保持できることです。Ad Grants 経由のコンバージョン計測が Cookie なしでも機能します。

---

## 2. GA4 キーイベントの登録（Admin API）

GA4 の「コンバージョン」は旧来の名称で、現在は「キーイベント」と呼びます。Admin API v1alpha を使って4種類を登録しました。

```python
# register_key_events.py
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha.types import KeyEvent

PROPERTY_ID = "531133170"  # GA4プロパティID

KEY_EVENTS = [
    {
        "event_name": "contact_form_complete",
        "counting_method": KeyEvent.CountingMethod.ONCE_PER_SESSION,
    },
    {
        "event_name": "public_asset_consultation_complete",
        "counting_method": KeyEvent.CountingMethod.ONCE_PER_SESSION,
    },
    {
        "event_name": "cta_click",
        "counting_method": KeyEvent.CountingMethod.ONCE_PER_EVENT,
    },
    {
        "event_name": "membership_register",
        "counting_method": KeyEvent.CountingMethod.ONCE_PER_SESSION,
    },
]

def register_key_events():
    client = AnalyticsAdminServiceClient()
    property_path = f"properties/{PROPERTY_ID}"

    for ke in KEY_EVENTS:
        key_event = KeyEvent(
            event_name=ke["event_name"],
            counting_method=ke["counting_method"],
        )
        result = client.create_key_event(
            parent=property_path,
            key_event=key_event,
        )
        print(f"登録完了: {result.event_name}")

if __name__ == "__main__":
    register_key_events()
```

`ONCE_PER_SESSION` と `ONCE_PER_EVENT` の使い分けが重要です。フォーム送信は1セッション1カウント、CTA クリックは複数回カウントする設計にしました。

---

## 3. Ad Grants PAUSED キーワードの復帰

最大のインパクトがあった施策です。Ad Grants は稼働中でも、キーワードが PAUSED になっていると予算を消化しません。

### PAUSED になる主な原因

Ad Grants には独自の品質基準があり、以下の条件でキーワードが自動 PAUSE されます。

- Quality Score（QS）が 3 以下
- 最低 CTR 基準（5%）を継続して下回る
- 広告グループに有効な広告がない

今回は QS=8 の「PPP PFI 入門」キーワードが PAUSED のまま放置されており、1,427 インプレッション分の機会を失っていました。

### 復帰の基準

すべての PAUSED キーワードを一律に ENABLE するのはリスクがあります。QS の低いキーワードを無理に有効化すると、アカウント全体の CTR が下がり、再び停止される悪循環に陥ります。

判断基準として「QS>=5 のみ即時 ENABLE」を採用しました。

```python
# adgrants_reactivate.py
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

CUSTOMER_ID = "YOUR_CUSTOMER_ID"  # ハイフンなし

def get_paused_keywords(client, customer_id):
    """PAUSED状態のキーワードをQS付きで取得"""
    ga_service = client.get_service("GoogleAdsService")
    query = """
        SELECT
            ad_group_criterion.criterion_id,
            ad_group_criterion.keyword.text,
            ad_group_criterion.quality_info.quality_score,
            ad_group_criterion.status,
            ad_group.id,
            campaign.id
        FROM ad_group_criterion
        WHERE
            ad_group_criterion.type = KEYWORD
            AND ad_group_criterion.status = PAUSED
            AND campaign.status = ENABLED
            AND ad_group.status = ENABLED
        ORDER BY ad_group_criterion.quality_info.quality_score DESC
    """
    response = ga_service.search(customer_id=customer_id, query=query)
    return list(response)

def enable_high_quality_keywords(client, customer_id, keywords, min_qs=5):
    """QS>=min_qsのキーワードをENABLEに変更"""
    ad_group_criterion_service = client.get_service("AdGroupCriterionService")
    operations = []

    for kw in keywords:
        criterion = kw.ad_group_criterion
        qs = criterion.quality_info.quality_score
        if qs >= min_qs:
            resource_name = criterion.resource_name
            op = client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = resource_name
            op.update.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.update_mask.paths.append("status")
            operations.append(op)
            print(f"  ENABLE予定: {criterion.keyword.text} (QS={qs})")

    if operations:
        result = ad_group_criterion_service.mutate_ad_group_criteria(
            customer_id=customer_id,
            operations=operations,
        )
        print(f"\n{len(result.results)}件のキーワードをENABLEに変更しました")
    return operations

if __name__ == "__main__":
    client = GoogleAdsClient.load_from_env()
    paused = get_paused_keywords(client, CUSTOMER_ID)
    print(f"PAUSED KW: {len(paused)}件")
    enable_high_quality_keywords(client, CUSTOMER_ID, paused, min_qs=5)
```

今回は20件を ENABLE し、予算消化率が 1.5% から 15〜30% に改善する見込みが立ちました。

### RSA（レスポンシブ検索広告）の改善

PAUSED 復帰と同時に、主要広告の RSA も改善しました。Ad Grants の RSA は「広告の有効性」スコアが「良好」以上でないと表示されにくい傾向があります。

改善のポイントは3点です。

1. 見出し15個を埋める（空きがあると有効性スコアが下がる）
2. 見出しにターゲットキーワードを含める
3. 説明文でユーザーの行動を促すCTAを明示する

---

## 4. FAQ Schema の自動生成

Google 検索でリッチリザルト（FAQ）を表示するための JSON-LD を自動生成する仕組みを実装しました。

### 実装方針

MDX 記事の本文から「Q:」「A:」のパターンを抽出し、FAQPage スキーマを自動付与します。手動でスキーマを書く必要がなく、記事を書くだけで対応できます。

```ts
// lib/faq-schema.ts
import { remark } from 'remark'
import remarkMdx from 'remark-mdx'
import { visit } from 'unist-util-visit'

interface FaqItem {
  question: string
  answer: string
}

export function extractFaqItems(content: string): FaqItem[] {
  const items: FaqItem[] = []
  const lines = content.split('\n')

  let currentQuestion: string | null = null
  let currentAnswer: string[] = []

  for (const line of lines) {
    const qMatch = line.match(/^#+\s+Q[:：]\s*(.+)/)
    const aMatch = line.match(/^(?:A[:：]|>\s*A[:：])\s*(.+)/)

    if (qMatch) {
      // 前の Q&A を保存
      if (currentQuestion && currentAnswer.length > 0) {
        items.push({
          question: currentQuestion,
          answer: currentAnswer.join(' ').trim(),
        })
      }
      currentQuestion = qMatch[1].trim()
      currentAnswer = []
    } else if (aMatch && currentQuestion) {
      currentAnswer.push(aMatch[1].trim())
    } else if (currentQuestion && currentAnswer.length > 0 && line.trim()) {
      // 回答の続き行
      if (!line.startsWith('#')) {
        currentAnswer.push(line.trim())
      }
    }
  }

  // 最後のアイテムを追加
  if (currentQuestion && currentAnswer.length > 0) {
    items.push({
      question: currentQuestion,
      answer: currentAnswer.join(' ').trim(),
    })
  }

  return items
}

export function buildFaqSchema(items: FaqItem[]) {
  if (items.length < 3) return null  // 3問未満は付与しない

  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: items.map((item) => ({
      '@type': 'Question',
      name: item.question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: item.answer,
      },
    })),
  }
}
```

記事ページコンポーネントでこの関数を呼び出し、`<script type="application/ld+json">` として出力します。

```tsx
// app/[slug]/page.tsx（抜粋）
import { extractFaqItems, buildFaqSchema } from '@/lib/faq-schema'

export default async function ArticlePage({ params }: { params: { slug: string } }) {
  const article = await getArticle(params.slug)
  const faqItems = extractFaqItems(article.content)
  const faqSchema = buildFaqSchema(faqItems)

  return (
    <>
      {faqSchema && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
        />
      )}
      {/* 記事本文 */}
    </>
  )
}
```

Q&A が3問以上ある記事に自動的にスキーマが付与されます。

---

## 5. 内部リンク孤立記事の解消

114本の記事のうち、49本が「どこからもリンクされていない孤立記事」でした。クローラーがたどれないため、インデックスされにくい状態です。

### 孤立記事の検出

Python で記事間のリンクグラフを構築し、孤立ノードを特定します。

```python
# detect_orphaned_articles.py
import re
from pathlib import Path
from typing import Set

CONTENT_DIR = Path("content/articles")

def build_link_graph() -> dict[str, Set[str]]:
    """記事間のリンクグラフを構築"""
    all_slugs = {p.stem for p in CONTENT_DIR.glob("*.mdx")}
    links: dict[str, Set[str]] = {slug: set() for slug in all_slugs}

    for mdx_file in CONTENT_DIR.glob("*.mdx"):
        content = mdx_file.read_text()
        # href="/slug" 形式のリンクを抽出
        found = re.findall(r'href=["\'/]([a-z0-9-]+)["\']', content)
        for target in found:
            if target in all_slugs:
                links[mdx_file.stem].add(target)

    return links

def find_orphaned_articles(links: dict[str, Set[str]]) -> Set[str]:
    """どこからもリンクされていない記事を検出"""
    all_slugs = set(links.keys())
    linked_to: Set[str] = set()
    for targets in links.values():
        linked_to.update(targets)
    return all_slugs - linked_to

if __name__ == "__main__":
    link_graph = build_link_graph()
    orphaned = find_orphaned_articles(link_graph)
    print(f"孤立記事: {len(orphaned)}件")
    for slug in sorted(orphaned):
        print(f"  - {slug}")
```

### 解消方法：双方向リンクの追加

孤立記事の解消は2方向で対処しました。

1. **ハブ記事への逆方向リンク追加** : カテゴリの概要記事に、関連する詳細記事へのリンクカードを追加
2. **孤立記事への関連記事セクション追加** : 孤立している記事の末尾に、同カテゴリの関連記事リンクを追加

今回は106ファイル・909行の変更で、孤立記事を49本から0本に解消しました。

---

## 6. SEOタイトルの最適化

ローンチ直後の記事タイトルは「読者にとって正確」だが「検索で見つかりにくい」パターンが多数ありました。

### 最適化の方針

検索意図に合わせたタイトルへの変更で、CTR の改善を狙います。

| Before | After |
|--------|-------|
| PPP・PFIの基礎知識 | PPP・PFIとは？官民連携の仕組みをわかりやすく解説 |
| 豊明市の事例 | 豊明市の廃校活用モデル：PPPで年間コスト40%削減した手法 |
| Park-PFIガイド | Park-PFI完全ガイド：公園の民間活力導入から申請まで |

変更は ja/en 両方のファイルを同時に行います。英語タイトルも検索意図に合わせて調整が必要です。

---

## 7. NL 登録 CTA の追加

コンバージョンポイントとして、ニュースレター登録のインライン CTA を新設しました。ホワイトペーパーのような重いコンテンツより、まずメールアドレスを取得する「マイクロCV」を先行させる判断です。

```tsx
// components/InlineNewsletterCta.tsx
'use client'

import { useState } from 'react'

interface Props {
  heading?: string
  description?: string
}

export function InlineNewsletterCta({
  heading = '週次ニュースレター',
  description = '公共資産活用の最新動向を毎週お届けします。',
}: Props) {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setStatus('loading')

    try {
      const res = await fetch('/api/newsletter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })

      if (!res.ok) throw new Error('登録に失敗しました')

      // GA4 キーイベント送信
      gtag('event', 'membership_register', {
        method: 'inline_cta',
      })

      setStatus('success')
    } catch {
      setStatus('error')
    }
  }

  if (status === 'success') {
    return <p className="text-green-600">登録が完了しました。</p>
  }

  return (
    <aside className="my-8 rounded-lg border border-gray-200 p-6">
      <h3 className="mb-2 text-lg font-semibold">{heading}</h3>
      <p className="mb-4 text-sm text-gray-600">{description}</p>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="your@email.com"
          required
          className="flex-1 rounded border px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={status === 'loading'}
          className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {status === 'loading' ? '送信中...' : '登録する'}
        </button>
      </form>
    </aside>
  )
}
```

---

## 施策実施後の想定インパクト

11施策の実装後、1ヶ月後の目標値として以下を設定しました。

| 指標 | 施策前 | 目標（1ヶ月後） |
|------|--------|--------------|
| 月間アクティブユーザー | 19 | 100+ |
| Ad Grants 予算消化率 | 1.5% | 15〜30% |
| 孤立記事数 | 49本 | 0本 |
| NL登録者数 | 1件 | 20件+ |
| キーイベント計測対象 | 0種 | 4種 |

---

## つまずきポイントと対処

### GSC オーナー確認は API では完結しない

Google Search Console のプロパティ追加は Admin API で実行できますが、「オーナー確認」は管理画面からの手動操作が必要です。API でプロパティを追加しても、検索データへのアクセス権限は付与されません。

GSC のサービスアカウントアクセス付与は「設定 > ユーザーと権限」から行います。

### Ad Grants は QS=3 以下のキーワードを有効化してはいけない

QS の低いキーワードを一律 ENABLE すると、アカウント全体の品質スコアが下がり、他の正常なキーワードまで PAUSE される連鎖が起きます。今回は QS=4 以下（13件）の復帰を保留し、RSA 改善後に再評価することにしました。

### SNS パイプラインの流用はサイト固有設定の確認が必須

別サイトのパイプラインを流用する際、`SITE_URL` や `CONTENT_TYPES` の設定が元のサイト仕様のまま残っていることがあります。今回は `CONTENT_TYPES` の配列とURL生成ロジックが別サイト向けのままで、公開してもURLが存在しない投稿が生成されていました。

---

## まとめ

ローンチ3週間・月間19ユーザーという状況から、11施策を一括実施しました。

施策の優先度を振り返ると、最大のインパクトは **Ad Grants PAUSED キーワードの復帰** でした。すでに QS=8 のキーワードが1,427インプレッションを獲得していたのに予算を使えていなかったのは、設定の見落としです。ローンチ後は「広告が稼働しているか」ではなく「キーワードが PAUSED になっていないか」を確認する習慣が重要です。

次点は **GA4 Consent Mode v2 の実装** です。Cookie 同意がないユーザーの行動をモデリングで補完できるため、実際のユーザー数より少なく見えていた可能性があります。

最後に、コンテンツの質が担保されていれば、技術的な施策は必ず効果を出せます。今回も平均セッション時間500秒という数値が示すとおり、コンテンツへの関心は最初からありました。届ける仕組みを整えることが、ローンチ直後の最優先課題です。

---

### 参考リソース

- [GA4 Consent Mode v2 公式ドキュメント](https://developers.google.com/tag-platform/security/guides/consent?hl=ja)
- [Google Ads API — AdGroupCriterion リファレンス](https://developers.google.com/google-ads/api/reference/rpc/v19/AdGroupCriterion)
- [Google Analytics Admin API — KeyEvent](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1alpha/properties.keyEvents)
- [Schema.org FAQPage](https://schema.org/FAQPage)

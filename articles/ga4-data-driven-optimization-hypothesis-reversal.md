---
title: "GA4実データが最適化の前提を覆した日 — Hero 4CTAのROIがゼロだった話"
emoji: "📊"
type: "tech"
topics: ["ga4", "analytics", "nextjs", "ux", "datadriven"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## Round 6 で何を作ったか

2026年3月後半から4月にかけて、自社の ISVD サイト（Next.js + MDX 構成）の改修を "100 patterns" と名付けて連続セッションで進めていた。

Round 6 の成果物は自分の中でもよく仕上がったと思っていた。

- Home ヒーローに4つの CTA を精密配置（About / Services / SDI 診断 / ペルソナ別ピル 4個）
- Hick's Law に基づいた選択肢数の検証フレームワークを構築
- アクセシビリティ Critical 4件修正（ARIA ラベル・コントラスト・フォーカス順序）

特に CTA 設計には力を入れた。ユーザーが最初に目にするヒーローセクションで迷わせないために、ペルソナ別のピルボタン（「学生の方」「支援者の方」「組織の方」「研究者の方」）を配置し、Hick's Law の $T = b \log_2(n + 1)$ に沿って選択肢数を制御した設計だ。

コードも動いた。Lighthouse スコアも上がった。「これは効く」と確信していた。

## Round 7 開始: 最初のクエリ

Round 7 のミッションは "GA4 baseline 観測"。改修の効果測定に入る前に、まず現状のユーザー行動を数値で把握しておこうというフェーズだった。

計測期間は 2026-03-20 から 2026-04-11 の約 3 週間。BigQuery に export された GA4 イベントデータ（`correlate-workspace.analytics_473560084.events_*`）を使って分析を始めた。

最初に書いたクエリはシンプルなページ別 PV 集計だった。

```sql
SELECT
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'page_location') AS page_location,
  COUNT(*) AS event_count,
  COUNT(DISTINCT user_pseudo_id) AS users
FROM
  `correlate-workspace.analytics_473560084.events_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
  AND event_name = 'page_view'
GROUP BY
  page_location
ORDER BY
  event_count DESC
LIMIT 20;
```

数字が返ってきた瞬間、何かがおかしいと気づいた。

## 最初の衝撃

Home `/` の数字が異常に少なかった。

| ページ | PV | ユーザー数 |
|--------|-----|-----------|
| `/` (Home) | 2,847 | 1,203 |
| `/articles/freshman-guide` | 8,943 | 4,521 |
| `/articles/bicycle-blue-ticket` | 6,104 | 3,892 |

「Home は 1,203 ユーザー。そこそこいる」と思った。しかし違和感があった。なぜ記事ページのほうが圧倒的に多いのか。Home から記事に流れるはずのユーザーが、記事の数だけいるのはおかしい。

ここで気づいた。 **ボットと SSR の混入だ。**

GA4 の BQ export では、`medium` が NULL のイベントが大量に含まれる。Googlebot、各種クローラー、サーバーサイドレンダリング時の ping、ヘルスチェックリクエスト。これらが `page_view` イベントとして記録されてしまうケースがある。

`medium IS NOT NULL` フィルタを追加して再実行した。

```sql
SELECT
  REGEXP_EXTRACT(
    (SELECT value.string_value FROM UNNEST(event_params)
     WHERE key = 'page_location'),
    r'https?://[^/]+(/.*)') AS page_path,
  COUNT(*) AS event_count,
  COUNT(DISTINCT user_pseudo_id) AS users
FROM
  `correlate-workspace.analytics_473560084.events_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
  AND event_name = 'page_view'
  AND (SELECT value.string_value FROM UNNEST(event_params)
       WHERE key = 'medium') IS NOT NULL
GROUP BY
  page_path
ORDER BY
  event_count DESC
LIMIT 20;
```

結果が変わった。大きく変わった。

| ページ | PV（フィルタ後） | ユーザー数 |
|--------|----------------|-----------|
| `/articles/freshman-guide` | 1,958 | 25 |
| `/articles/bicycle-blue-ticket` | 1,492 | 11 |
| `/articles/labor-standard-act` | 987 | 8 |
| `/` (Home) | 20 | 1 |

Home の実ユーザーは **10日で 20 PV / 1 user** だった。

フィルタ前は 2,847 PV いたはずが、実ユーザーは 20 PV。 **93% 以上がボットまたはノイズだった。**

## 発見の連鎖

衝撃を引きずりながら、次に CTA クリックの分析に進んだ。Round 6 で丁寧に設計したヒーロー CTA が、実際にどれだけクリックされているかを見る。

```sql
SELECT
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'cta_placement') AS placement,
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'cta_label') AS label,
  COUNT(*) AS click_count
FROM
  `correlate-workspace.analytics_473560084.events_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
  AND event_name = 'cta_click'
  AND (SELECT value.string_value FROM UNNEST(event_params)
       WHERE key = 'medium') IS NOT NULL
GROUP BY
  placement, label
ORDER BY
  click_count DESC;
```

全期間の `cta_click` は **9件**。

| placement | label | クリック数 |
|-----------|-------|-----------|
| floating_bar | お問い合わせ | 5 |
| floating_bar | 無料相談を予約 | 4 |
| hero | About | 0 |
| hero | Services | 0 |
| hero | SDI診断 | 0 |
| hero_pill | 学生の方 | 0 |
| hero_pill | 支援者の方 | 0 |
| hero_pill | 組織の方 | 0 |
| hero_pill | 研究者の方 | 0 |

**Hero CTA のクリック数はゼロ。全期間を通じて一件も押されていなかった。**

全 9 件のクリックは、フローティングバーによるものだった。Round 6 で精密設計したヒーローの 4CTA は、誰も見ていなかった。

## 前提の反証

このデータが意味することを整理した。

自分が Round 6 で前提としていたのは「ユーザーは Home に来て、ヒーロー CTA を見て、次のページに進む」というジャーニーだった。だから CTA を最適化することに意味があった。Hick's Law を適用して選択肢を絞ることにも意義があった。

しかし実データが示した事実はまったく別のものだった。

**ユーザーは Home に来ていない。記事に直接着地している。**

内訳を確認すると、流入元はほぼすべてが Google Ads（AdGrants）と organic 検索だった。AdGrants で freshman-guide や bicycle-blue-ticket などの記事ページに直接流入し、記事を読んで離脱する。このパターンが実態だった。

Home を経由するユーザーは 10日に 1 人だ。その 1 人に向けて Hick's Law を適用した CTA を設計することは、工学的には正しくても、ビジネス的には意味がない。

優先すべき最適化対象は最初から記事ページだった。

## 意思決定の方向転換

このデータを受けて、Round 7 で方針転換を実施した。判断の過程をそのまま記録しておく。

**廃棄した前提:**

- Home ヒーローが主要なコンバージョン経路である
- ヒーロー CTA の最適化が最優先事項である
- ペルソナ別のセグメントが Home 経由で分岐する

**採用した新方針:**

- 記事ページが主戦場。具体的には `freshman-guide`（1,958 PV / 25 users）と `bicycle-blue-ticket`（1,492 PV / 11 users）の 2本
- NextActions コンポーネント（関連記事 + CTA）を高 exit 記事の末尾に展開
- Home ヒーローは defensive fix（A11y 維持・表示崩れ修正）のみで凍結

コードで言えば、こういう変化だ。Round 6 で実装した Hero コンポーネントには手を入れず、代わりに記事ページに NextActions を追加した。

```tsx
// before: Home hero CTA に注力（Round 6）
<HeroSection>
  <CTAGroup>
    <CTAButton href="/about" variant="primary">About</CTAButton>
    <CTAButton href="/services" variant="secondary">Services</CTAButton>
    <CTAButton href="/sdi" variant="secondary">SDI診断</CTAButton>
  </CTAGroup>
  <PersonaPills personas={["学生の方", "支援者の方", "組織の方", "研究者の方"]} />
</HeroSection>

// after: 高トラフィック記事に NextActions を展開（Round 7）
<ArticleLayout>
  <ArticleContent />
  <NextActions
    relatedArticles={relatedArticles}
    primaryCTA={{ label: "無料相談を予約", href: "/contact" }}
  />
</ArticleLayout>
```

数値で言えば、最適化の対象 PV が 20（Home / 1 user）から 1,958 + 1,492 = 3,450（2 記事 / 36 users）に切り替わった。同じ工数でリーチできる実ユーザー数が **172倍** になる。

## なぜ「前提の検証」を先にしなかったか

振り返ると、Round 6 の失敗パターンはシンプルだ。 **「Home が重要」という仮説を検証せずに最適化を始めた。**

「ホームページはサイトの玄関口」という通念がある。だから Home を最適化するのは自然に思える。しかし通念は仮説にすぎない。小規模サイト + AdGrants 運用という文脈では、検索意図に合った記事ページへの直接流入が主流になることは十分ありうる。

実際、AdGrants のキャンペーン構造を確認すると、出稿しているキーワードはすべて記事コンテンツに対応するロングテールだった。「大学 就活 準備」「自転車 通勤 保険」これらのクエリで流入したユーザーが Home に来る理由はない。

**最適化の前に実データでユーザーの動線を確認する。** これだけのことだ。しかし自分はやらなかった。

「GA4 の baseline 観測」を Round 7 の最初のタスクとして設定したのは意識的だったが、それは改修後の効果測定のためだと思っていた。改修前の前提検証としての役割に気づいていなかった。結果として Round 6 の作業 7〜8時間分の ROI がゼロになった。

## データの前処理: medium IS NOT NULL の重要性

今回の発見で最も実用的な知見は、`medium IS NOT NULL` フィルタの重要性だ。

GA4 の BigQuery export をそのまま集計すると、現実とかけ離れた数字が出る。フィルタ前後の比較を整理しておく。

| 指標 | フィルタ前 | フィルタ後（実ユーザー） | 差分 |
|------|-----------|----------------------|------|
| Home PV | 2,847 | 20 | -99.3% |
| Home ユーザー数 | 1,203 | 1 | -99.9% |
| 記事PV（freshman） | 8,943 | 1,958 | -78.1% |
| 記事PV（bicycle） | 6,104 | 1,492 | -75.6% |

Home の 99% 以上がノイズだったのは極端なケースかもしれないが、記事ページでも 78% のノイズが含まれていた。フィルタなしで CVR を計算すると、分母が 5 倍近く膨らむ。施策の効果判定が完全に狂う。

`medium` は GA4 のデフォルトディメンションではなく、event_params の中に格納されている。標準の GA4 UI では簡単にフィルタできないため、BQ export × SQL で分析する際には必ず含めるようにした。

```sql
-- 実ユーザー判定の標準フィルタ
AND (
  SELECT value.string_value
  FROM UNNEST(event_params)
  WHERE key = 'medium'
) IS NOT NULL

-- page_location から page_path を抽出（クエリパラメータ除外）
REGEXP_EXTRACT(
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'page_location'),
  r'https?://[^/]+(/?[^?#]*)'
) AS page_path
```

なお `page_path` は GA4 BQ schema では直接取れない。`page_location`（完全 URL）か `page_referrer` として記録されるため、REGEXP_EXTRACT で path 部分を切り出す必要がある。

## AdGrants 前提の LP 設計論

今回の分析で副産物として得られた知見がある。AdGrants 運用サイトの LP 設計についてだ。

AdGrants は Google の非営利向け広告プログラムで、月間 $10,000 相当のテキスト広告を無料で配信できる。ただし制約がある。

- 検索広告のみ（ディスプレイ不可）
- 最低 CTR 5% を維持しないとアカウント停止
- LP のクオリティスコア（QS）が低いと出稿できない

この制約が LP 設計に直結する。CTR 5% を維持するためには、検索意図に完全に合致した広告文 + LP が必要だ。「大学 就活 準備」で検索したユーザーに Home を見せても QS は上がらない。freshman-guide 記事を LP として設定し、記事の冒頭でユーザーの検索意図を直接受け取る構造が最適になる。

つまり AdGrants 前提では、 **記事 = LP** という設計が自然に導出される。Home はブランド検索流入か、記事を読んだ後に自発的に来るユーザーのためのハブとして機能する。CTA 最適化の主戦場は記事ページになる。

これは今回の GA4 データとも整合する。実ユーザーの着地点が記事に集中していたのは偶然ではなく、AdGrants の構造が生み出した必然だった。

## 教訓の整理

Round 6 から Round 7 の経験を通じて、自分の作業ルールとして追加したことを記録しておく。

**1. 最適化の着手前に対象ページの実トラフィック数値を確認する**

「このページが重要」という認識はすべて仮説として扱い、GA4 BQ の実数で検証してから工数を投入する。実ユーザー 1 人/10日のページに 8時間かける判断は、データなしには気づけない。

**2. medium IS NOT NULL を分析の標準フィルタにする**

フィルタなしの数字は参考程度に留め、意思決定には必ず実ユーザーフィルタ済みの数値を使う。

**3. cta_click の placement 別集計を定点観測に含める**

CTA を追加するたびに `placement` パラメータを設定し、定点観測クエリで全 placement の click 数を追う。「誰も押していない CTA」の発見を早期化する。

**4. AdGrants サイトは記事 = LP として設計する**

ヒーロー CTA の精度より、各記事末尾の NextActions 設計のほうが CVR に直結する。

## おわりに

「データドリブン」という言葉はよく使われるが、実際には「データで前提を覆す経験」を意識的に積まないと、データを後付けの正当化に使うだけになりやすい。

今回は Round 6 の作業時間が ROI ゼロになったが、Round 7 の最初に GA4 を見たことで方針転換ができた。もし半年後に「Hero CTA の効果が出ない」と気づいていたら、ダメージはさらに大きかった。

データが仮説を覆す経験は、短期的には痛い。しかし次の最適化の確度を上げる。そのサイクルを速く回すことが、小規模サイト運営での唯一の武器だと思っている。

---

## 付録: GA4 BQ クエリ集

実際に使ったクエリをまとめておく。同じ構成（Next.js + GA4 BQ export）を使っているサイト運営者の参考になれば。

### ページ別実ユーザー集計

```sql
SELECT
  REGEXP_EXTRACT(
    (SELECT value.string_value FROM UNNEST(event_params)
     WHERE key = 'page_location'),
    r'https?://[^/]+(/?[^?#]*)') AS page_path,
  COUNT(*) AS page_views,
  COUNT(DISTINCT user_pseudo_id) AS users
FROM
  `{project}.{dataset}.events_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
  AND event_name = 'page_view'
  AND (SELECT value.string_value FROM UNNEST(event_params)
       WHERE key = 'medium') IS NOT NULL
GROUP BY page_path
ORDER BY page_views DESC
LIMIT 30;
```

### CTA クリック placement 別集計

```sql
SELECT
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'cta_placement') AS placement,
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'cta_label') AS label,
  COUNT(*) AS clicks,
  COUNT(DISTINCT user_pseudo_id) AS unique_users
FROM
  `{project}.{dataset}.events_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
  AND event_name = 'cta_click'
  AND (SELECT value.string_value FROM UNNEST(event_params)
       WHERE key = 'medium') IS NOT NULL
GROUP BY placement, label
ORDER BY clicks DESC;
```

### セッション別流入チャネル分析

```sql
SELECT
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'medium') AS medium,
  (SELECT value.string_value FROM UNNEST(event_params)
   WHERE key = 'source') AS source,
  COUNT(DISTINCT CONCAT(user_pseudo_id,
    CAST((SELECT value.int_value FROM UNNEST(event_params)
          WHERE key = 'ga_session_id') AS STRING))) AS sessions
FROM
  `{project}.{dataset}.events_*`
WHERE
  _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
  AND (SELECT value.string_value FROM UNNEST(event_params)
       WHERE key = 'medium') IS NOT NULL
GROUP BY medium, source
ORDER BY sessions DESC;
```

### ページ別直帰率（出口率）

```sql
WITH session_pages AS (
  SELECT
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params)
     WHERE key = 'ga_session_id') AS session_id,
    REGEXP_EXTRACT(
      (SELECT value.string_value FROM UNNEST(event_params)
       WHERE key = 'page_location'),
      r'https?://[^/]+(/?[^?#]*)') AS page_path,
    COUNT(*) AS page_views_in_session
  FROM
    `{project}.{dataset}.events_*`
  WHERE
    _TABLE_SUFFIX BETWEEN '20260320' AND '20260411'
    AND event_name = 'page_view'
    AND (SELECT value.string_value FROM UNNEST(event_params)
         WHERE key = 'medium') IS NOT NULL
  GROUP BY user_pseudo_id, session_id, page_path
),
session_counts AS (
  SELECT
    user_pseudo_id,
    session_id,
    COUNT(DISTINCT page_path) AS unique_pages
  FROM session_pages
  GROUP BY user_pseudo_id, session_id
)
SELECT
  sp.page_path,
  COUNT(*) AS landing_sessions,
  COUNTIF(sc.unique_pages = 1) AS bounce_sessions,
  ROUND(COUNTIF(sc.unique_pages = 1) / COUNT(*) * 100, 1) AS bounce_rate
FROM session_pages sp
JOIN session_counts sc
  ON sp.user_pseudo_id = sc.user_pseudo_id
  AND sp.session_id = sc.session_id
GROUP BY sp.page_path
HAVING COUNT(*) >= 5
ORDER BY landing_sessions DESC
LIMIT 20;
```

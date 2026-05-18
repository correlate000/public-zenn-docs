---
title: "営業メール送信ポリシーを民事構成 + 実装で OSS 化した話 — Next.js + BQ + Discord PII redaction"
emoji: "📨"
type: "tech"
topics: ["nextjs", "typescript", "bigquery", "discord", "oss"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## TL;DR

- ISVD（一般社団法人社会構想デザイン機構）で「営業メール送信ポリシー」を 2026-05-25 から運用開始しました
- 民法第 709 条不法行為構成を中核に、無断営業メール送信者へ手数料・損害賠償を請求する規程
- 規程文書 + Next.js + TypeScript 実装 + BigQuery DDL + Discord 通知パイプライン一式を **CC BY 4.0 + MIT** のデュアルライセンスで OSS 公開
- リポジトリ: <https://github.com/correlate000/sales-email-policy>

法理側の解説は ISVD サイトの記事に書きました。本記事は **エンジニアリング側の設計** をまとめます。

- 法理側解説: <https://isvd.or.jp/columns/sales-email-policy-civil-approach>

## なぜこの問題は技術で解けないのか（と、それでも実装が必要な理由）

「無断営業メールが消耗する」問題は、誰もが知っているが構造的には未解決の領域です。既存対策はすべて「対症療法」に留まります。

| 対策 | 機能 | 限界 |
|------|------|------|
| 特電法（特定電子メール法） | 行政取締り | 個別救済機能なし、商業性が要件 |
| スパムフィルタ（Gmail 等） | 受信側で隔離 | 送信側の経済合理性は不変 |
| ブラックリスト | ドメイン単位ブロック | カテゴリ偽装には弱い |
| 個別ブロック設定 | 都度対応 | 累積で時間を奪われる構造は同じ |

すべての対策が **送信コスト約 0 円 / 受信コスト約 100 円** という経済構造的非対称を温存しています。送信が経済合理的である限り、新規送信者は供給され続けます。

ISVD の規程は、この非対称を **「受信側に発生する実費を、送信側に転嫁する民事的請求権」** によって組み替える設計です。技術で解けない問題に、技術 + 規程の組み合わせで解を作ります。

## アーキテクチャ全体像

```
[送信者]                                        [ISVD 内部]
   │
   ├─ Contact form 経由 ─┐
   │                      │
   │                      ▼
   │             ┌──────────────────┐
   │             │  /api/contact    │
   │             │  - Zod validation│
   │             │  - 14 パターン辞書│
   │             │    カテゴリ偽装検知│
   │             └────────┬─────────┘
   │                      │
   │                      ├─ Resend SMTP ──→ ISVD inbox (full body)
   │                      │
   │                      ├─ Discord webhook ──→ Discord channel (PII redacted)
   │                      │                       └ SHA-256/8 domain hash のみ
   │                      │
   │                      └─ BigQuery INSERT ──→ contact_violations (UUID PK)
   │
   └─ 公表メールアドレス ─→ ISVD inbox + 人的トリアージ
```

要点:
- フォーム経由は **同意 + バリデーション + 通知** の 3 段
- Discord 通知は **PII（個人情報）完全排除設計**
- BigQuery で違反履歴を一元集計（PARTITION + CLUSTER）
- 公表メールアドレス向けは別経路、不法行為構成で扱う

## 1. フォーム同意 UI — a11y 込みの設計

「同意した」を後日に立証するためには、ユーザーが意識的にチェックを入れた事実を残す必要があります。WCAG 3.3.1（Error Identification）と 4.1.3（Status Messages）に準拠した a11y 連携で実装しました。

```tsx
// src/app/[locale]/contact/_page-client.tsx (抜粋)

const [ackError, setAckError] = useState<"" | "main" | "paid">("");
const ackMainRef = useRef<HTMLInputElement>(null);

const handleSubmit = async (e: FormEvent) => {
  e.preventDefault();

  if (!ackMain) {
    setAckError("main");
    ackMainRef.current?.focus();
    ackMainRef.current?.scrollIntoView({
      block: "center",
      behavior: "smooth",
    });
    return;
  }
  // ... 送信処理
};

return (
  <input
    ref={ackMainRef}
    type="checkbox"
    aria-invalid={ackError === "main"}
    aria-describedby={ackError === "main" ? "ack-main-error" : undefined}
    checked={ackMain}
    onChange={(e) => {
      setAckMain(e.target.checked);
      if (e.target.checked) setAckError("");
    }}
  />
);
```

ポイント:
- `aria-invalid` + `aria-describedby` でスクリーンリーダーにエラー伝達
- `focus()` + `scrollIntoView({ block: "center" })` で視覚的にも明示
- エラー解消条件（チェックON）でリアルタイムに `aria-invalid` 解除

これで「同意なしで submit してきた」というケースを送信者側の責任で消化させ、フォーム側に同意の事実を残します。

## 2. カテゴリ偽装検知 — 14 パターン辞書

フォームには「カテゴリ」選択肢があります（例: 「協業のご相談」「メディア取材」「営業」など）。**営業以外のカテゴリで送信しつつ本文は営業内容** という偽装が頻出するため、サーバ側で本文と subject の自然言語シグナルを照合します。

```typescript
// src/lib/contact-spam-filter.ts (抜粋)

const SALES_PATTERNS = [
  /(ご提案|ご紹介)\s*(させて|いたし)/,
  /(無料デモ|無料相談|資料ダウンロード)/,
  /(SEO|Web 制作|マーケティング)\s*(支援|代行|サービス)/,
  /(弊社|当社).{0,30}(サービス|プロダクト|ツール)/,
  /(契約|受注|ご導入)\s*(の)?\s*(機会|ご案内)/,
  // ... 全 14 パターン
] as const;

export function detectSalesContentInNonSalesCategory(
  body: string,
  subject: string,
  category: string
): boolean {
  if (SALES_CATEGORIES.includes(category)) return false;
  const text = `${subject}\n${body}`;
  const matches = SALES_PATTERNS.filter((p) => p.test(text));
  return matches.length >= 2;
}
```

ポイント:
- **2 つ以上のシグナル一致** で営業判定（誤判定低減）
- 偽装が検知されると規程上 **¥20,000**（通常の 2 倍）の損害賠償対象
- セーフハーバー（協業文脈の具体的言及がある場合）は別ロジックで除外

## 3. Discord 通知の PII 完全排除

カジュアルに見落とされやすいですが、**個人情報を含む通知を Discord に流すと、Discord 社（米国）が第三者にあたります**。個人情報保護法 27 条（第三者提供）の同意取得が必要になり、運用負荷が爆発します。

そこで通知は SHA-256 ハッシュドメインのみを Discord に送る設計にしました。

```typescript
// src/app/api/contact/route.ts (抜粋)

import crypto from "crypto";

const email = body.email;
const domain = email.split("@")[1] ?? "unknown";
const domainHash = crypto
  .createHash("sha256")
  .update(domain)
  .digest("hex")
  .slice(0, 8);

const tag = isSales ? "🔴営業" : isPotentialSpam ? "🟡要確認" : "🟢通常";
const billable = isSales ? "対象（フォーム経由・¥10,000〜）" : "対象外";

await notifyDiscord([
  `**${tag}** ${subject || "件名なし"}`,
  `送信ドメイン (SHA-256/8): ${domainHash}`,
  `請求対象 (規程上): ${billable}`,
  `処理: ISVD 管理 contact@ の通知メール本文で確認`,
  `規約: <https://isvd.or.jp/sales-email-policy>`,
].join("\n"));
```

- 個人氏名・メールアドレス・本文の **すべてが Discord に渡らない**
- フルデータは ISVD 管理 inbox（Resend SMTP 経由）にのみ届く
- Discord はトリアージ通知（請求対象かどうかのフラグ）のみ
- ハッシュドメイン同士の重複比較は可能なので、リピート送信の判定はできる

プライバシーポリシーには Discord 通知の設計を明示しました（GDPR Article 6(1)(f) 法的利益 + APPI 27 条準拠）。

## 4. BigQuery で違反履歴を集計

違反の累積を時系列で追えるよう、BigQuery にテーブルを作りました。

```sql
-- db/migrations/2026-05-18-contact-violations.sql

CREATE TABLE IF NOT EXISTS `isvd-prod.compliance.contact_violations` (
  violation_id STRING DEFAULT GENERATE_UUID() NOT NULL,
  occurred_at TIMESTAMP NOT NULL,
  domain_hash STRING NOT NULL,           -- SHA-256/8
  subject STRING,                         -- 件名
  category_declared STRING,               -- フォーム選択カテゴリ
  category_inferred STRING,               -- ロジック判定
  sales_signals INT64,                    -- 一致パターン数
  billable_amount NUMERIC,
  invoice_status STRING,                  -- pending / sent / paid / objected / withdrawn
  notes STRING
)
PARTITION BY DATE(occurred_at)
CLUSTER BY domain_hash, invoice_status;
```

```sql
-- db/views/contact-violations-summary.sql

CREATE OR REPLACE VIEW `isvd-prod.compliance.v_violations_monthly_summary` AS
SELECT
  DATE_TRUNC(occurred_at, MONTH) AS month,
  COUNT(*) AS violations,
  COUNTIF(invoice_status = 'paid') AS paid,
  COUNTIF(invoice_status = 'objected') AS objected,
  SUM(IF(invoice_status = 'paid', billable_amount, 0)) AS revenue
FROM `isvd-prod.compliance.contact_violations`
GROUP BY month
ORDER BY month DESC;
```

- **PARTITION BY DATE**: 月次サマリーが高速 / コスト低
- **CLUSTER BY domain_hash, invoice_status**: リピート送信ドメインの検索が高速
- **DEFAULT GENERATE_UUID()**: 冪等 INSERT パターンで重複生成回避

## 5. 国税庁法人番号 Web-API 連携

請求書を発行する段階で、相手方法人の正式名称・住所が必要です。国税庁の法人番号 Web-API v4 を利用します。

```python
# operations/scripts/lookup-corporate-number.py (抜粋)

import os
import requests

NTA_APP_ID = os.environ.get("NTA_APP_ID")  # 必須

def _require_app_id():
    if not NTA_APP_ID:
        raise SystemExit(
            "NTA_APP_ID is required. "
            "Register at https://www.houjin-bangou.nta.go.jp/webapi/"
        )

def lookup_by_name(name: str) -> list[dict]:
    _require_app_id()
    url = "https://api.houjin-bangou.nta.go.jp/4/name"
    params = {
        "id": NTA_APP_ID,
        "name": name,
        "type": "12",   # CSV
        "history": "0",
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return _parse_csv(r.text)
```

- `NTA_APP_ID` 環境変数の有無を **冒頭で明示エラー** にして silent fail を防止
- フォールバック: 登記情報提供サービスでの手動取得手順を README に併記

## 6. dual license の使い分け

OSS 公開時に license をどう貼るかで迷いました。**規程文書とコードは性質が違います**。

| 領域 | ライセンス | 理由 |
|------|---------|------|
| `policy/`, `operations/*.md`, `docs/` | CC BY 4.0 | 規程の引用・改変・再頒布を促進、改変箇所明示で透明性 |
| `implementation/`, `database/`, `operations/scripts/` | MIT | コード流用の自由度を最大化、商用利用も歓迎 |

リポジトリルートに `LICENSE-DOCS` と `LICENSE-CODE` を分けて配置し、`README.md` で適用範囲を明示しました。

## 弁護士法第 72 条との関係（重要）

エンジニアリング側だけ作って終わりにはなりません。**他組織が本規程を採用する場合、当法人が紛争代理を直接担うと弁護士法第 72 条違反のリスクがあります**。

設計上の役割分担:
- 当法人: 規程整備・実装統合・運用設計の **伴走**
- 弁護士・弁護士法人: 規程レビュー・紛争代理（請求書発行後の対応、訴訟、支払督促）

有償導入支援メニュー（¥100K〜 / ¥500K〜 / ¥1.5M〜）にはこの境界を明示しました。

## まとめ

- 「営業メール対策の OSS 化」と一言で言うと軽く聞こえますが、**規程設計 + 法的構成 + a11y + PII redaction + dual license + 弁護士法境界** までを一貫して作るとそれなりに考えどころは多かったです
- AI（Claude）を「規程草案の検討パートナー」として活用しつつ、最終判断・公開責任は当法人が持つプロセスにしました
- 採用報告・批判レビュー・実装改善 PR、いずれも歓迎です

リポジトリ: <https://github.com/correlate000/sales-email-policy>
法理側解説: <https://isvd.or.jp/columns/sales-email-policy-civil-approach>
ISVD: <https://isvd.or.jp>

採用 / 専門家レビューのご連絡: <https://isvd.or.jp/contact>

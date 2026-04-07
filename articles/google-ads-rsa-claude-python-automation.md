---
title: "Google Ads RSAをClaude + Pythonで自動生成する仕組みを作った"
emoji: "✍️"
type: "tech"
topics: ["googleads", "claude", "python", "adgrants", "automation"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

ISVD（一般社団法人社会構想デザイン機構）の Ad Grants 運用で、RSA（レスポンシブ検索広告）の広告文を Claude が生成し、Python スクリプトが Google Ads API で更新する仕組みを作りました。

「プロンプト入力 → 広告文生成 → バリデーション → dry-run → 本番実行」という一気通貫フローです。

この記事では、Claude スキルファイルの設計と、LLM を「広告コピーライター」として組み込む実装を中心に解説します。

---

## 全体フロー

```
ユーザー（Naoya）
    │
    │ /rsa スキル呼び出し
    ▼
Claude Code（スキルファイル読み込み）
    │
    │ プロンプトに従い広告文を生成
    ▼
update_rsa.py --dry-run（バリデーション確認）
    │
    │ バリデーション通過
    ▼
update_rsa.py --no-confirm（本番実行）
    │
    │ REMOVE → CREATE
    ▼
Google Ads API
```

手動で書いていた「ヘッドライン15本 × 30幅以内、ディスクリプション4本 × 90幅以内」の制約を Claude が守りながら生成し、Python でバリデーション後に API 投入します。

---

## Claude スキルファイル（/rsa）の設計

Claude Code のスキルファイルは `~/.claude/skills/` に配置した Markdown ファイルです。`/rsa` と入力すると Claude がこのファイルを読み込み、指示に従って動作します。

```markdown
# /rsa — RSA広告文生成・更新スキル

## 目的
Google Ads API を使って ISVD Ad Grants の RSA を更新する。
Claude が広告文を生成し、update_rsa.py が API 経由で更新する。

## 事前確認
1. 対象広告グループ名を確認する
2. ランディングページの URL と訴求ポイントを把握する
3. 既存広告文（直近のもの）があれば参照する

## 広告文生成ルール

### ヘッドライン（15本生成、最低3本必須）
- 表示幅 30 以内（全角=2、半角=1）
- 重複禁止
- キーワードを含む（1〜3本）
- 数字・実績・ベネフィットを含む（複数）
- 行動喚起（CTA）を含む（1〜2本）

### ディスクリプション（4本生成）
- 表示幅 90 以内
- 訴求の角度をそれぞれ変える（機能説明・ベネフィット・安心感・CTA）

### 禁止事項
- 誇大表現（「日本最高」「絶対」等）
- 薬機法・景表法に抵触する表現
- 競合他社名の言及

## 実行手順
1. 広告文を生成してユーザーに提示する
2. ユーザーが承認したら dry-run を実行する
   ```
   python update_rsa.py --dry-run --ad-group-id {ad_group_id}
   ```
3. バリデーション通過を確認してから本番実行する
   ```
   python update_rsa.py --no-confirm --ad-group-id {ad_group_id}
   ```
4. 実行結果をセッション記録に残す

## 注意
- 広告文の最終承認は必ずユーザーが行う
- dry-run 未確認での本番実行は禁止
- 実行後は Google Ads UI で掲載状況を確認すること
```

このスキルファイルの特徴は以下の3点です：

1. **生成ルールを明文化**：文字幅制限・本数制限・禁止表現をスキルファイルに書く
2. **実行フローを順序立てて定義**：「生成 → 承認 → dry-run → 本番」の順を守らせる
3. **Human in the Loop を設計に組み込む**：最終承認はユーザーが行う

---

## 広告文の生成プロンプト設計

スキルファイルに記載されたルールをもとに、Claude は以下のような形式で広告文を提案します。

```
【広告グループ】キャリア診断
【LP URL】https://career.isvd.or.jp/diagnosis

■ ヘッドライン（15本）
1. AI が適職を診断します（16幅）
2. 3分でキャリア相談【無料】（17幅）
3. 社会人の職業適性診断（13幅）
4. 転職前に強みを把握しよう（15幅）
5. フリーランス向き？診断で確認（18幅）
6. 無料・AIキャリア診断ツール（16幅）
7. 働き方の悩みをAIが解決（15幅）
8. 副業・独立の適性を診断（14幅）
9. 公務員・会社員の転職相談（14幅）
10. キャリアに迷ったらまず診断（16幅）
11. ISVDが提供する無料診断（14幅）
12. 次のキャリアを一緒に考える（16幅）
13. 職業適性を3分で可視化（14幅）
14. 登録不要・すぐ使える診断（15幅）
15. 今すぐ無料で始める（11幅）

■ ディスクリプション（4本）
1. AIがあなたの強みと志向を分析し、向いている職業をご提案します。転職・副業・独立を検討中の方に。（49幅）
2. 社会人経験者向けの職業適性診断。3分の質問回答で、あなたのキャリアの方向性が見えてきます。（50幅）
3. ISVDが運営する信頼性の高いキャリア支援。個人情報の登録不要で、今すぐ診断を開始できます。（50幅）
4. 診断結果をもとに、専門家への相談や求人情報のご案内も可能です。まずはAI診断をお試しください。（52幅）
```

ユーザーが確認・修正した後、スクリプトに渡す JSON ファイルに変換します。

---

## 広告文データの JSON フォーマット

```json
{
  "ad_group_id": "123456789",
  "final_url": "https://career.isvd.or.jp/diagnosis",
  "headlines": [
    "AI が適職を診断します",
    "3分でキャリア相談【無料】",
    "社会人の職業適性診断",
    "転職前に強みを把握しよう",
    "フリーランス向き？診断で確認",
    "無料・AIキャリア診断ツール",
    "働き方の悩みをAIが解決",
    "副業・独立の適性を診断",
    "公務員・会社員の転職相談",
    "キャリアに迷ったらまず診断",
    "ISVDが提供する無料診断",
    "次のキャリアを一緒に考える",
    "職業適性を3分で可視化",
    "登録不要・すぐ使える診断",
    "今すぐ無料で始める"
  ],
  "descriptions": [
    "AIがあなたの強みと志向を分析し、向いている職業をご提案します。転職・副業・独立を検討中の方に。",
    "社会人経験者向けの職業適性診断。3分の質問回答で、あなたのキャリアの方向性が見えてきます。",
    "ISVDが運営する信頼性の高いキャリア支援。個人情報の登録不要で、今すぐ診断を開始できます。",
    "診断結果をもとに、専門家への相談や求人情報のご案内も可能です。まずはAI診断をお試しください。"
  ]
}
```

---

## スクリプトの引数設計

```python
import argparse
import json
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Claude生成の広告文をGoogle Ads APIでRSA更新する"
    )
    parser.add_argument(
        "--content-file",
        required=True,
        help="広告文JSONファイルのパス"
    )
    parser.add_argument(
        "--customer-id",
        default="YOUR_CUSTOMER_ID",
        help="Google Ads 顧客ID"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="バリデーションのみ実行（APIへの書き込みなし）"
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="確認プロンプトをスキップ（スクリプト・Claude実行用）"
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="REMOVEをスキップ（REMOVE失敗後のリカバリ用）"
    )
    return parser.parse_args()


def load_content_file(path: str) -> dict:
    """JSON形式の広告文ファイルを読み込む。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] ファイルが見つかりません: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSONのパースに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)
```

---

## dry-run による安全実行

Claude スキルファイルでは「dry-run 未確認での本番実行は禁止」と定義しています。これにより、Claude 自身が必ず dry-run → 確認 → 本番の順序を守ります。

```bash
# Step 1: dry-run でバリデーション確認
python update_rsa.py \
  --content-file rsa_career_diagnosis.json \
  --dry-run

# 出力例:
# [INFO] ヘッドライン: 15本
# [INFO] ディスクリプション: 4本
# [CHECK] AI が適職を診断します → 16幅 ✓
# [CHECK] 3分でキャリア相談【無料】 → 17幅 ✓
# ... (全件チェック)
# [DRY-RUN] バリデーション通過。APIへの書き込みはスキップします。

# Step 2: ユーザーが目視確認・承認

# Step 3: 本番実行
python update_rsa.py \
  --content-file rsa_career_diagnosis.json \
  --no-confirm
```

---

## Ad Grants 運用での注意点

### 広告文の品質スコアと CTR の関係

Ad Grants では CTR 5%以上の維持が必要です。広告文の関連性が低いと CTR が下がり、アカウント停止のリスクがあります。

Claude への生成指示には「キーワードとの関連性」を必ず含めてください：

```markdown
## 広告文生成時の追加指示（Ad Grants向け）

- ターゲットキーワード（例：「転職 向いていない 特徴」）が
  ヘッドライン1〜3本に含まれるようにする
- ランディングページの訴求内容と一致する表現にする
- クリックしたユーザーが「期待外れ」にならない誠実な表現を優先する
```

### 広告文の承認待ち期間

新しい RSA を作成すると「承認待ち」ステータスになります。通常1営業日以内に審査が完了しますが、ポリシー違反が含まれると「制限付き」または「掲載不可」になります。

---

## 実際に動かした結果

ISVD Ad Grants（予算 $329/日）で、このパイプラインを使って広告文を更新した結果：

- 更新作業時間：以前の手動作業（30〜60分）→ Claude + スクリプト（5〜10分）
- バリデーションエラー発生率：手動時代の約15% → スクリプト化後 0%（文字数オーバーなし）
- 広告強度スコア：「平均的」→「良好」に改善（ヘッドライン多様性の向上）

---

## まとめ

| 工程 | ツール | 役割 |
|------|--------|------|
| 広告文生成 | Claude（/rsa スキル） | ルールに従った文案作成 |
| バリデーション | Python（display_width） | 文字幅・本数チェック |
| dry-run 確認 | update_rsa.py | 安全確認ゲート |
| API 更新 | Google Ads API | REMOVE → CREATE |
| 実行記録 | セッション記録 | 変更履歴の保管 |

LLM を「広告コピーライター」として使う発想は、繰り返し発生する定型的な創造性タスクを自動化する上で非常に有効です。スキルファイルで「生成ルール」と「実行フロー」を定義することで、品質と安全性を両立できます。

---

## 関連記事

- [Google Ads RSA をClaude + Pythonで自動更新する実装詳解](./google-ads-rsa-claude-python-auto-update)
- [Google Ads API で広告が消えた15秒間 — REMOVE+CREATE競合エラーのリカバリ手順](./google-ads-api-rsa-remove-create-race-condition)
- [Google Ads API v23でMAXIMIZE_CLICKSが使えない問題とTARGET_SPENDへの移行](./google-ads-api-v23-maximize-clicks-target-spend)

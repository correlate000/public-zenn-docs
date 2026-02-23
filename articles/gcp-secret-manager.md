---
title: "GCP Secret Manager 実践ガイド ─ Cloud Run・GitHub Actions・ローカル開発でのシークレット管理"
emoji: "🔑"
type: "tech"
topics: ["gcp", "secretmanager", "cloudrun", "セキュリティ", "python"]
published: false
publication_name: "correlate_dev"
---

## はじめに

APIキーやデータベースの接続文字列を `.env` ファイルで管理しているプロジェクトは多いと思います。手軽で分かりやすい反面、`.env` ファイルには次のようなリスクがあります。

- `.gitignore` の設定ミスで Git に誤ってコミットしてしまうリスク
- チームメンバーへの共有がチャットやメールになりがちで、漏洩経路が増える
- 誰がいつ値を変更・アクセスしたかの監査ログが残らない
- ローテーションのたびに全員が `.env` を更新する必要がある

GCP Secret Manager はこれらの課題をまとめて解決するマネージドサービスです。シークレットのバージョン管理・アクセスログの自動記録・IAMによる細粒度の権限制御が可能で、Cloud Run や GitHub Actions との統合も充実しています。

本記事では次のスコープをカバーします。

- シークレットの作成・更新・バージョン管理（gcloud コマンド）
- Cloud Run への環境変数マウントと Volume マウント
- Python SDK（`google-cloud-secret-manager`）での読み取り
- GitHub Actions でのシークレット参照
- ローカル開発での代替手段（ADC + direnv）
- IAM ロール設計とローテーションのベストプラクティス

GCP プロジェクトと Cloud Run の基本的な使い方を知っている方を対象読者として想定しています。

---

## 全体アーキテクチャ

まず本記事で扱うシークレット管理の全体フローを確認します。

```mermaid
flowchart LR
    subgraph "ローカル開発"
        DEV[開発者] -->|ADC| SM
    end
    subgraph "CI/CD"
        GA[GitHub Actions] -->|OIDC / WIF| SA[Service Account]
        SA --> SM
    end
    subgraph "本番"
        CR[Cloud Run] -->|--set-secrets| SM
        SM -->|環境変数として注入| CR
    end
    SM[Secret Manager] -->|監査ログ| LOG[Cloud Logging]
```

3つの実行環境（ローカル・GitHub Actions・Cloud Run）がそれぞれ異なる認証方法で Secret Manager にアクセスし、アクセスログは Cloud Logging に集約されます。

---

## 1. シークレットの基本操作

### シークレットの作成

まず Secret Manager API を有効化します。

```bash
gcloud services enable secretmanager.googleapis.com \
  --project={PROJECT_ID}
```

シークレットを作成します。シークレット自体はコンテナ（名前と設定）で、値（バージョン）とは分離されています。

```bash
# レプリケーションポリシーを automatic に設定して作成
gcloud secrets create my-api-key \
  --replication-policy="automatic" \
  --project={PROJECT_ID}
```

特定のリージョンにのみ保存したい場合は `--locations` を使います。

```bash
gcloud secrets create my-db-url \
  --replication-policy="user-managed" \
  --locations="asia-northeast1" \
  --project={PROJECT_ID}
```

### バージョンの追加（値の設定）

シークレットに値を追加するにはバージョンを作成します。

```bash
# 標準入力から追加（値がターミナル履歴に残らない）
echo -n "postgresql://user:pass@host:5432/db" | \
  gcloud secrets versions add my-db-url \
    --data-file=- \
    --project={PROJECT_ID}

# ファイルから追加
gcloud secrets versions add my-api-key \
  --data-file=./api-key.txt \
  --project={PROJECT_ID}
```

`echo -n` の `-n` は末尾改行を除去するために必要です。改行が混入すると認証エラーの原因になります。

### バージョンの確認・管理

```bash
# バージョン一覧の確認
gcloud secrets versions list my-api-key \
  --project={PROJECT_ID}

# 最新バージョンの値を確認
gcloud secrets versions access latest \
  --secret="my-api-key" \
  --project={PROJECT_ID}

# 特定バージョンの値を確認
gcloud secrets versions access 2 \
  --secret="my-api-key" \
  --project={PROJECT_ID}

# バージョンを無効化（値はそのまま残る）
gcloud secrets versions disable 1 \
  --secret="my-api-key" \
  --project={PROJECT_ID}

# バージョンを完全削除（復元不可）
gcloud secrets versions destroy 1 \
  --secret="my-api-key" \
  --project={PROJECT_ID}
```

---

## 2. Cloud Run への環境変数マウント

Cloud Run でシークレットを環境変数として参照する方法が `--set-secrets` オプションです。コンテナ起動時に Secret Manager から値を取得し、指定した環境変数名で注入します。

```bash
gcloud run deploy my-service \
  --image="asia-northeast1-docker.pkg.dev/{PROJECT_ID}/my-repo/my-service:latest" \
  --set-secrets="DATABASE_URL=my-db-url:latest,API_KEY=my-api-key:latest" \
  --region=asia-northeast1 \
  --project={PROJECT_ID}
```

`my-db-url:latest` の形式は `シークレット名:バージョン番号` です。`latest` を指定すると常に最新バージョンが使われます。デプロイ時に固定したい場合はバージョン番号（`my-db-url:3` など）を指定します。

コンソールから設定する場合は「コンテナ・変数とシークレット」タブの「シークレット」セクションで追加できます。

複数シークレットを設定する場合のデプロイ例です。

```bash
gcloud run deploy my-service \
  --image="asia-northeast1-docker.pkg.dev/{PROJECT_ID}/my-repo/my-service:latest" \
  --set-secrets="\
DATABASE_URL=my-db-url:latest,\
REDIS_URL=my-redis-url:latest,\
OPENAI_API_KEY=openai-api-key:latest,\
STRIPE_SECRET_KEY=stripe-secret-key:latest" \
  --region=asia-northeast1 \
  --project={PROJECT_ID}
```

Cloud Run サービスのサービスアカウントに `roles/secretmanager.secretAccessor` ロールが付与されている必要があります（後述の IAM セクションで詳しく説明します）。

---

## 3. Cloud Run への Volume マウント

シークレットの値をファイルとしてコンテナ内のパスにマウントする方法もあります。JSON 形式のサービスアカウントキーや証明書ファイルを扱う場合に便利です。

```bash
gcloud run deploy my-service \
  --image="asia-northeast1-docker.pkg.dev/{PROJECT_ID}/my-repo/my-service:latest" \
  --set-secrets="/secrets/service-account.json=gcp-sa-key:latest" \
  --region=asia-northeast1 \
  --project={PROJECT_ID}
```

この設定でコンテナ内の `/secrets/service-account.json` にシークレットの値がファイルとして書き込まれます。

Python アプリケーションからはファイルパスで読み込みます。

```python
import json

def load_service_account_key(path: str = "/secrets/service-account.json") -> dict:
    with open(path, "r") as f:
        return json.load(f)
```

環境変数マウントと Volume マウントの使い分けの目安は次の通りです。

| ユースケース | 推奨方式 |
|------------|---------|
| 接続文字列・API キーなどの文字列値 | 環境変数マウント |
| JSON 形式のサービスアカウントキー | Volume マウント |
| TLS 証明書（PEM/CRT ファイル） | Volume マウント |
| 設定ファイル（YAML/TOML） | Volume マウント |

---

## 4. Python SDK での読み取り

アプリケーションコードから直接 Secret Manager を参照する場合は `google-cloud-secret-manager` ライブラリを使います。

```bash
pip install google-cloud-secret-manager
# または
uv add google-cloud-secret-manager
```

基本的な読み取り関数です。

```python
from google.cloud import secretmanager
from google.api_core.exceptions import GoogleAPICallError, NotFound


def access_secret(
    project_id: str,
    secret_id: str,
    version_id: str = "latest",
) -> str:
    """
    Secret Manager からシークレットの値を取得する。

    Args:
        project_id: GCP プロジェクト ID
        secret_id: シークレット名
        version_id: バージョン番号または "latest"（デフォルト: "latest"）

    Returns:
        シークレットの文字列値

    Raises:
        NotFound: シークレットまたはバージョンが存在しない場合
        GoogleAPICallError: API 呼び出しに失敗した場合
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"

    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except NotFound:
        raise NotFound(f"Secret '{secret_id}' (version: {version_id}) が見つかりません")
    except GoogleAPICallError as e:
        raise GoogleAPICallError(f"Secret Manager API エラー: {e}") from e
```

実際のアプリケーションでは、起動時に一度だけ読み込んでキャッシュするパターンが一般的です。

```python
import os
from functools import lru_cache
from google.cloud import secretmanager


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")


@lru_cache(maxsize=None)
def get_secret(secret_id: str, version_id: str = "latest") -> str:
    """起動時に一度だけ読み込み、以降はキャッシュから返す。"""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


# 使用例
DATABASE_URL = get_secret("my-db-url")
OPENAI_API_KEY = get_secret("openai-api-key")
```

`lru_cache` によりプロセスが生きている間はメモリキャッシュが効くため、シークレット更新後はアプリケーションの再デプロイが必要になります。頻繁な更新が不要なシークレットにはこの方法が適しています。

---

## 5. GitHub Actions でのシークレット参照

GitHub Actions からは `google-github-actions/get-secretmanager-secrets` アクションを使います。Workload Identity Federation（WIF）でキーレス認証を行う構成を推奨します。

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write  # WIF に必要

    steps:
      - uses: actions/checkout@v4

      # GCP への認証（Workload Identity Federation）
      - name: Authenticate to GCP
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      # Secret Manager からシークレットを取得
      - name: Get secrets from Secret Manager
        id: secrets
        uses: google-github-actions/get-secretmanager-secrets@v2
        with:
          secrets: |-
            DATABASE_URL:projects/{PROJECT_ID}/secrets/my-db-url/versions/latest
            API_KEY:projects/{PROJECT_ID}/secrets/my-api-key/versions/latest

      # 取得したシークレットを後続ステップで使用
      - name: Run integration tests
        env:
          DATABASE_URL: ${{ steps.secrets.outputs.DATABASE_URL }}
          API_KEY: ${{ steps.secrets.outputs.API_KEY }}
        run: |
          python -m pytest tests/integration/
```

`{PROJECT_ID}` の部分は GitHub Secrets の変数（`${{ secrets.GCP_PROJECT_ID }}`）に置き換えることを推奨します。

WIF の設定方法は [gcp-workload-identity-vercel](/articles/gcp-workload-identity-vercel) の記事で詳しく解説しています。

---

## 6. ローカル開発での代替

ローカル開発でも Secret Manager に直接アクセスする方法と、`.env` で代替する方法の2パターンがあります。

### Application Default Credentials（ADC）で直接アクセス

```bash
# ADC をセットアップ
gcloud auth application-default login

# プロジェクトを設定
gcloud config set project {PROJECT_ID}
```

ADC が設定されていれば、Python SDK は自動的にローカルの認証情報を使用して Secret Manager にアクセスします。コード変更は不要です。

```python
# Cloud Run 上でも、ローカルでも、同じコードが動く
DATABASE_URL = get_secret("my-db-url")
```

ただし、ローカルからアクセスするために本番のシークレットと分離した開発用シークレットを用意することを強く推奨します。

### direnv + .envrc でローカル設定を管理

Secret Manager へのアクセス権限が不要な開発作業や、シンプルな環境では `direnv` を使った `.env` の管理も有効です。

```bash
# direnv のインストール（macOS）
brew install direnv

# .envrc にシェル変数を設定
cat > .envrc << 'EOF'
export DATABASE_URL="postgresql://user:pass@localhost:5432/mydb_dev"
export API_KEY="dev-api-key-xxxxxxxxxxxx"
EOF

# .envrc を許可
direnv allow .
```

`.envrc` は `.gitignore` に追加し、実際の値をコミットしないよう注意してください。

```bash
# .gitignore に追加
echo ".envrc" >> .gitignore
```

開発・本番環境のシークレット分離の考え方は次の図の通りです。

```mermaid
flowchart TB
    subgraph "本番環境（推奨）"
        CR[Cloud Run] -->|--set-secrets| SM_PROD[Secret Manager\nproject: my-project-prod]
        SM_PROD --> S_PROD[本番シークレット\nDB URL, API Key 等]
    end
    subgraph "開発環境（推奨 A: ADC）"
        LOCAL_A[ローカル開発] -->|ADC| SM_DEV[Secret Manager\nproject: my-project-dev]
        SM_DEV --> S_DEV[開発用シークレット\n本番と完全分離]
    end
    subgraph "開発環境（推奨 B: direnv）"
        LOCAL_B[ローカル開発] --> ENVRC[.envrc\n.gitignore 済み]
        ENVRC --> S_LOCAL[ローカル専用の値\n開発用 DB 等]
    end
    subgraph "開発環境（非推奨）"
        LOCAL_C[ローカル開発] --> DOTENV[.env ファイル\n⚠ 誤コミットリスク]
    end
```

---

## 7. アクセス制御（IAM ロール設計）

Secret Manager で使用する主要な IAM ロールは3つです。

| ロール | 権限内容 | 推奨付与先 |
|--------|---------|-----------|
| `roles/secretmanager.secretAccessor` | バージョンの値の読み取りのみ | Cloud Run SA、GitHub Actions SA |
| `roles/secretmanager.secretVersionManager` | バージョンの追加・無効化・削除 | デプロイ用 SA、CI/CD パイプライン |
| `roles/secretmanager.admin` | シークレットの作成・削除、ポリシー変更 | インフラ管理者のみ |

Cloud Run のサービスアカウントに `secretAccessor` ロールを付与する例です。

```bash
# Cloud Run サービスアカウントを確認
gcloud run services describe my-service \
  --region=asia-northeast1 \
  --format="value(spec.template.spec.serviceAccountName)" \
  --project={PROJECT_ID}

# secretAccessor ロールを付与
gcloud projects add-iam-policy-binding {PROJECT_ID} \
  --member="serviceAccount:my-service-sa@{PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

特定のシークレットのみアクセスを許可する場合は、プロジェクトレベルではなくシークレットレベルで IAM を設定します。

```bash
# シークレット個別に IAM を設定（最小権限の原則）
gcloud secrets add-iam-policy-binding my-db-url \
  --member="serviceAccount:my-service-sa@{PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project={PROJECT_ID}
```

この方法ではサービスアカウントが `my-db-url` のみアクセス可能で、他のシークレットは読み取れません。複数のサービスが同一プロジェクトに共存する場合に有効な設計です。

---

## 8. ローテーションのベストプラクティス

### 手動ローテーションの手順

新しいバージョンを追加し、古いバージョンを無効化する「ブルーグリーン方式」が安全です。

```bash
# Step 1: 新しい値でバージョンを追加
echo -n "new-api-key-value-xxxxxxxx" | \
  gcloud secrets versions add my-api-key \
    --data-file=- \
    --project={PROJECT_ID}

# Step 2: バージョン一覧で番号を確認
gcloud secrets versions list my-api-key --project={PROJECT_ID}

# Step 3: Cloud Run を新バージョンで再デプロイ（または latest のまま再デプロイ）
gcloud run deploy my-service \
  --image="asia-northeast1-docker.pkg.dev/{PROJECT_ID}/my-repo/my-service:latest" \
  --set-secrets="API_KEY=my-api-key:latest" \
  --region=asia-northeast1 \
  --project={PROJECT_ID}

# Step 4: 動作確認後、古いバージョンを無効化
gcloud secrets versions disable 1 \
  --secret="my-api-key" \
  --project={PROJECT_ID}
```

### Pub/Sub を使った自動ローテーション通知

Secret Manager のローテーション機能では、設定した日時に Pub/Sub トピックへ通知を送れます。Cloud Functions や Cloud Run で通知を受け取り、外部サービスのキーを自動更新する仕組みを構築できます。

```bash
# ローテーションスケジュールを設定（90日ごとに通知）
gcloud secrets update my-api-key \
  --rotation-period="7776000s" \
  --next-rotation-time="2026-05-23T00:00:00Z" \
  --topics="projects/{PROJECT_ID}/topics/secret-rotation" \
  --project={PROJECT_ID}
```

### バージョン管理の指針

| 方針 | 説明 |
|------|------|
| `latest` 参照 | 常に最新値を使いたい場合。ローテーション後の再デプロイが不要 |
| 固定バージョン参照 | 変更を意図的にコントロールしたい場合。デプロイ時にバージョンを更新 |
| 古いバージョンの保持 | ロールバック用に1〜2世代前は `disabled`（削除せず）にしておくと安全 |

---

## まとめ

Secret Manager を使うことで、シークレット管理に関する多くの課題を解決できます。

- `.env` ファイルの誤コミットリスクをなくせる
- Cloud Run・GitHub Actions・ローカル開発で一貫した管理が可能
- IAM ロールで最小権限のアクセス制御を実現できる
- Cloud Logging にアクセスログが自動記録され、監査に対応できる

導入のハードルが高そうに見えますが、実際には `gcloud secrets create` と `--set-secrets` の2コマンドで Cloud Run との連携が完成します。まずは新規プロジェクトの1つのシークレットを Secret Manager に移行するところから始めてみてください。

既存プロジェクトへの移行は、`.env` の値を Secret Manager に登録して `--set-secrets` に切り替えるだけなので、アプリケーションコードの変更は不要です。

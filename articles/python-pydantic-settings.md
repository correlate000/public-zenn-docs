---
title: "Pydantic Settings 実践ガイド ─ 型安全な設定管理とCloud Run・Secret Manager対応"
emoji: "⚙️"
type: "tech"
topics: ["python", "pydantic", "fastapi", "gcp", "cloudrun"]
published: false
publication_name: "correlate_dev"
---

## はじめに ─ os.environ 直接読み取りの問題点

PythonアプリケーションでAPIキーやデータベース接続文字列を管理するとき、最初に思いつく方法は `os.environ` の直接読み取りではないでしょうか。

```python
# よくある実装例（問題あり）
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./db.sqlite3")
DEBUG = os.environ.get("DEBUG", "false")  # 文字列で返ってくる
MAX_CONNECTIONS = int(os.environ.get("MAX_CONNECTIONS", "10"))  # 手動でキャスト
```

この方法には、いくつかの課題があります。

- 型が `str` で固定されるため、`bool` や `int` への変換を手動で行う必要がある
- 必須の環境変数が未設定でも、アプリケーション起動時にエラーが出ない（実行中に初めてクラッシュする）
- 設定値の定義が分散しやすく、どこで何を読んでいるか把握しづらい
- バリデーション（例: URLの形式チェック、数値の範囲チェック）を自前で書く必要がある

`python-dotenv` を使えば `.env` ファイルからの読み込みはできますが、型安全性やバリデーションの問題は解決しません。

この記事では、Pydantic v2 の `pydantic-settings` を使って、これらの問題をすべて解決する方法を解説します。

---

## pydantic-settings のセットアップ

`pydantic-settings` は Pydantic v2 から独立したパッケージとして分離されています。

```bash
pip install pydantic-settings
# または
uv add pydantic-settings
```

Python バージョンは 3.8 以上、pydantic-settings は v2.x（2024年現在の最新）を前提に解説します。

---

## 基本的な BaseSettings 定義

まず最もシンプルな例から始めます。

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    gcp_project_id: str
    api_key: SecretStr = Field(..., alias="SECRET_API_KEY")
    debug: bool = False
    database_url: str = "sqlite:///./db.sqlite3"
    max_connections: int = Field(default=10, ge=1, le=100)


settings = Settings()
```

ポイントを整理します。

`model_config` は `SettingsConfigDict` でまとめて指定します。これは Pydantic v2 のスタイルです。v1 の `class Config:` は非推奨になりました。

`env_prefix="APP_"` を指定すると、`gcp_project_id` フィールドは `APP_GCP_PROJECT_ID` という環境変数から読み込まれます。`alias` を使えば任意の環境変数名にマッピングできます。

`SecretStr` 型を使うと、ログ出力時に値が `**********` とマスクされます。シークレット値には積極的に使いましょう。

型変換は自動で行われます。環境変数 `APP_DEBUG=true` は `bool` の `True` として読み込まれます。`APP_MAX_CONNECTIONS=50` は `int` の `50` になります。バリデーション（`ge=1, le=100`）も自動で適用されます。

`gcp_project_id` はデフォルト値がないため必須項目です。環境変数が未設定だと `Settings()` の時点で `ValidationError` が発生します。起動時に問題が発覚するので、実行中の予期しないクラッシュを防げます。

---

## .env ファイルとの連携

開発環境では `.env` ファイルを使うのが一般的です。

```ini
# .env（Gitリポジトリには含めない）
APP_GCP_PROJECT_ID=my-project-dev
SECRET_API_KEY=dev-api-key-xxxxx
APP_DEBUG=true
APP_DATABASE_URL=postgresql://localhost:5432/mydb_dev
APP_MAX_CONNECTIONS=5
```

`model_config` の `env_file=".env"` で自動的に読み込まれます。

複数の `.env` ファイルを指定することもできます。

```python
model_config = SettingsConfigDict(
    env_file=(".env", ".env.local"),  # タプルで複数指定
    env_file_encoding="utf-8",
    # 後から指定したファイルが優先される
)
```

設定の優先順位は次のとおりです。

```mermaid
flowchart TD
    A[Settings初期化引数\n最優先] --> B[環境変数\n例: export APP_DEBUG=true]
    B --> C[.env.local ファイル\nローカル上書き用]
    C --> D[.env ファイル\nデフォルト定義]
    D --> E[フィールドのデフォルト値\n最低優先度]
    style A fill:#ff6b6b,color:#fff
    style B fill:#ffa94d,color:#fff
    style C fill:#ffd43b,color:#333
    style D fill:#69db7c,color:#333
    style E fill:#74c0fc,color:#333
```

この優先順位を理解しておくと、「CI環境では環境変数で上書き、ローカルでは `.env.local` で個人設定」という運用が自然に実現できます。

---

## 環境ごとの設定分岐（dev/staging/production）

本番・ステージング・開発で設定を切り替えるパターンです。

```python
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class BaseAppSettings(BaseSettings):
    """全環境共通の設定"""
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file_encoding="utf-8",
    )

    app_env: str = Field(default="development", pattern="^(development|staging|production)$")
    gcp_project_id: str
    debug: bool = False


class DevelopmentSettings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    debug: bool = True
    database_url: str = "sqlite:///./db.sqlite3"


class ProductionSettings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file_encoding="utf-8",
        # 本番では .env ファイルを使わない（環境変数のみ）
    )

    debug: bool = False
    database_url: str  # 必須（デフォルトなし）
    api_key: SecretStr


def get_settings_class():
    env = os.environ.get("APP_APP_ENV", "development")
    if env == "production":
        return ProductionSettings
    elif env == "staging":
        return ProductionSettings  # stagingはproductionと同じクラスで運用
    return DevelopmentSettings


@lru_cache
def get_settings():
    SettingsClass = get_settings_class()
    return SettingsClass()
```

---

## ネストされた設定（Database・API Settings等）

実際のアプリケーションでは、設定を論理的にグループ化したいケースが多くあります。

```python
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "mydb"
    user: str = "postgres"
    password: SecretStr
    pool_size: int = 5

    @property
    def url(self) -> str:
        password = self.password.get_secret_value()
        return f"postgresql://{self.user}:{password}@{self.host}:{self.port}/{self.name}"


class ExternalAPISettings(BaseModel):
    base_url: str = "https://api.example.com"
    timeout_seconds: int = 30
    max_retries: int = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # APP_DATABASE__HOST=db.example.com
    )

    debug: bool = False
    database: DatabaseSettings
    external_api: ExternalAPISettings = ExternalAPISettings()
```

`env_nested_delimiter="__"` を設定すると、環境変数 `APP_DATABASE__HOST=db.example.com` が `settings.database.host` にマッピングされます。

```ini
# .env
APP_DATABASE__HOST=localhost
APP_DATABASE__PORT=5432
APP_DATABASE__NAME=mydb
APP_DATABASE__USER=postgres
APP_DATABASE__PASSWORD=secret-password

APP_EXTERNAL_API__BASE_URL=https://api.example.com
APP_EXTERNAL_API__TIMEOUT_SECONDS=60
```

利用時は次のように使います。

```python
settings = Settings()

# データベースURLの取得
print(settings.database.url)
# postgresql://postgres:secret-password@localhost:5432/mydb

# SecretStr の値取得
raw_password = settings.database.password.get_secret_value()
```

---

## Cloud Run での Secret Manager 統合

Cloud Run では、Secret Manager のシークレットを2つの方法で渡せます。

### 方法1: 環境変数としてマウント（シンプル）

Cloud Run のサービス定義で Secret Manager のシークレットを環境変数にマッピングします。

```yaml
# cloud-run-service.yaml の一部
env:
  - name: APP_DATABASE__PASSWORD
    valueFrom:
      secretKeyRef:
        name: db-password
        key: latest
  - name: APP_API_KEY
    valueFrom:
      secretKeyRef:
        name: api-key
        key: latest
```

この方法では、pydantic-settings 側の実装は通常の環境変数読み込みと変わりません。

### 方法2: ファイルシステムにマウント（セキュアな方法）

Secret Manager のシークレットをファイルとしてマウントし、pydantic-settings で読み込む方法です。

```yaml
# cloud-run-service.yaml の一部
volumes:
  - name: db-password-volume
    secret:
      secret: db-password
volumeMounts:
  - name: db-password-volume
    mountPath: /secrets
```

```python
from pathlib import Path
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def read_secret_file(path: str) -> str:
    """Secret Managerのボリュームマウントされたシークレットを読む"""
    secret_path = Path(path)
    if secret_path.exists():
        return secret_path.read_text().strip()
    raise ValueError(f"Secret file not found: {path}")


class CloudRunSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file_encoding="utf-8",
    )

    gcp_project_id: str
    debug: bool = False

    # 環境変数 APP_DATABASE_PASSWORD_FILE でファイルパスを渡す
    database_password_file: str = "/secrets/db-password"

    @property
    def database_password(self) -> SecretStr:
        return SecretStr(read_secret_file(self.database_password_file))
```

### Cloud Run へのデプロイ例

```bash
gcloud run deploy my-service \
  --image gcr.io/my-project/my-service:latest \
  --set-env-vars "APP_GCP_PROJECT_ID=my-project,APP_APP_ENV=production" \
  --set-secrets "APP_DATABASE__PASSWORD=db-password:latest,APP_API_KEY=api-key:latest" \
  --region asia-northeast1
```

---

## テスト時の設定オーバーライド

テストで本番の環境変数や `.env` ファイルを使うのは危険です。テスト専用の設定を確実に使う方法を解説します。

### pytest での monkeypatch を使う方法

```python
# tests/test_settings.py
import pytest
from myapp.config import Settings


def test_settings_default(monkeypatch):
    # 環境変数を強制設定
    monkeypatch.setenv("APP_GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("APP_DEBUG", "true")

    # .env ファイルを無効化して環境変数のみ使用
    settings = Settings(_env_file=None)

    assert settings.gcp_project_id == "test-project"
    assert settings.debug is True


def test_settings_validation_error(monkeypatch):
    # 必須項目が未設定の場合に ValidationError が発生することを確認
    monkeypatch.delenv("APP_GCP_PROJECT_ID", raising=False)

    with pytest.raises(Exception):  # ValidationError
        Settings(_env_file=None)
```

### テスト用フィクスチャの共通化

```python
# tests/conftest.py
import pytest
from myapp.config import Settings


@pytest.fixture
def test_settings(monkeypatch):
    """テスト用の設定を返すフィクスチャ"""
    monkeypatch.setenv("APP_GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("APP_DATABASE__HOST", "localhost")
    monkeypatch.setenv("APP_DATABASE__PASSWORD", "test-password")
    monkeypatch.setenv("APP_DEBUG", "true")

    return Settings(_env_file=None)


# tests/test_database.py
def test_database_connection(test_settings):
    assert test_settings.database.host == "localhost"
    assert test_settings.debug is True
```

### lru_cache と組み合わせる場合の注意点

`get_settings()` に `@lru_cache` をつけている場合、テスト間でキャッシュが残ります。`cache_clear()` で明示的にクリアします。

```python
# tests/conftest.py
from myapp.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """各テスト前にSettingsキャッシュをクリアする"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

---

## FastAPI との統合（Depends での使用）

FastAPI では `Depends` を使った依存性注入が推奨されています。

```python
# app/config.py
from functools import lru_cache
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
    )

    gcp_project_id: str
    debug: bool = False
    database_url: str
    api_key: SecretStr = Field(..., alias="SECRET_API_KEY")
    allowed_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```python
# app/main.py
from fastapi import FastAPI, Depends
from app.config import Settings, get_settings

app = FastAPI()


@app.get("/health")
def health_check(settings: Settings = Depends(get_settings)):
    return {
        "status": "ok",
        "env": settings.gcp_project_id,
        "debug": settings.debug,
    }


@app.get("/api/data")
def get_data(settings: Settings = Depends(get_settings)):
    # settings.api_key.get_secret_value() でキー値を取得
    api_key = settings.api_key.get_secret_value()
    # ... API呼び出し
    return {"data": "..."}
```

`@lru_cache` により `Settings()` は初回のみ生成されます。テスト時は `app.dependency_overrides` でオーバーライドします。

```python
# tests/test_main.py
from fastapi.testclient import TestClient
from app.main import app
from app.config import Settings, get_settings


def get_test_settings():
    return Settings(
        gcp_project_id="test-project",
        debug=True,
        database_url="sqlite:///./test.db",
        _env_file=None,
        # alias を使っているフィールドは直接フィールド名で渡せる
        api_key="test-api-key",
    )


def test_health_check():
    app.dependency_overrides[get_settings] = get_test_settings
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["env"] == "test-project"

    app.dependency_overrides.clear()
```

---

## まとめ

`pydantic-settings` を使うと、次のことが実現できます。

| 課題 | pydantic-settings での解決策 |
|------|------------------------------|
| 型変換を手動で行っている | フィールド型宣言で自動変換 |
| 必須項目の未設定を実行中に発見する | 起動時に `ValidationError` で即時検出 |
| バリデーションを自前で実装している | `Field(ge=..., le=...)` 等で宣言的に記述 |
| シークレット値がログに出る | `SecretStr` で自動マスク |
| 設定の定義が分散している | `BaseSettings` クラスに集約 |
| テスト時に本番設定が混入する | `_env_file=None` + `monkeypatch` で分離 |

os.environ 直接読み取りから `pydantic-settings` への移行は、既存コードへの影響も最小限です。まずはアプリケーションのエントリーポイントに `Settings` クラスを1つ定義するところから始めてみてください。

Cloud Run や Secret Manager との統合も、環境変数経由でシームレスに対応できます。型安全な設定管理は、バグを未然に防ぐ最もコスパの高い投資の1つだと実感しています。

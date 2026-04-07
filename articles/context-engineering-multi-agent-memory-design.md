---
title: "コンテキストエンジニアリング実践 — 商用マルチエージェントのメモリ設計3原則"
emoji: "🧠"
type: "tech"
topics: ["llm", "agent", "claude", "architecture", "python"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

「プロンプトエンジニアリング」という言葉が一般化して久しいですが、LLMをプロダクションで動かす現場では別の問題が浮上してきています。

**コンテキストをどう設計・管理するか**

Jeremy Dalyが発表した「Context Engineering for Commercial Agent Systems」は、マルチテナント商用エージェントに求められる設計原則を体系化した論文です。本記事ではこの論文を軸に、個人・小規模チームのエージェント開発に転用できる知見を整理します。

自分自身も `correlate-workspace` で複数のClaude Codeエージェントを運用しており、Obsidian + MEMORY.md + セッション管理という独自体制を構築してきました。その実体験と照合しながら解説していきます。

---

## コンテキストエンジニアリングとは何か

プロンプトエンジニアリングが「何を言うか」に焦点を当てるのに対し、コンテキストエンジニアリングは **「どの情報を、いつ、どの形式でモデルに渡すか」** を設計する技術です。

商用エージェントで問題になるのは次の3点です：

1. **マルチテナント分離** — 複数のユーザーや組織のデータが混ざってはいけない
2. **決定論的な再現性** — 同じ入力に対して同じ出力が得られる保証
3. **コストの予測可能性** — コンテキストウィンドウの肥大化によるトークン爆発の防止

これら3つが「非交渉的原則（Non-negotiable Principles）」として定義されています。

---

## 原則1：構造的隔離（Structural Isolation）

### 問題

マルチテナント環境では、あるテナントのコンテキストが別のテナントのレスポンスに影響を与えるリスクがあります。単純な文字列結合でプロンプトを組み立てているシステムでは、テナントAのデータがテナントBの処理に漏洩することがあります。

### 解決策：スコープ×タイプの二次元分類

論文ではメモリを次の二次元で分類することを提案しています：

**スコープ軸（誰が使うか）**

| スコープ | 対象 | 例 |
|---------|------|-----|
| Global | システム全体 | 利用規約、システムポリシー |
| Tenant | 組織単位 | 会社の製品カタログ、ブランドガイドライン |
| User | 個人単位 | ユーザーの好み、過去の設定 |
| Session | 会話単位 | 今回の会話履歴、一時的な状態 |

**タイプ軸（何を格納するか）**

| タイプ | 内容 | 永続性 |
|-------|------|--------|
| Policy | ルール・制約 | 永続 |
| Factual | 事実情報（RAG対象） | 半永続 |
| Episodic | 過去の出来事・手順 | 中程度 |

### 個人開発での実装例

自分の場合、この分類を以下のように実装しています：

```
~/.claude/CLAUDE.md         → Global/Policy（全セッション共通ルール）
~/.claude/SOUL.md           → Global/Policy（AIペルソナ定義）
~/.claude/USER.md           → User/Factual（ユーザー環境情報）
~/.claude/projects/*/MEMORY.md → Tenant/Episodic（プロジェクト固有の知見）
06_sessions/yyyy/mm/*.md   → Session/Episodic（セッション記録）
```

スコープの混在を防ぐため、`CLAUDE.md` にはプロジェクト固有情報を書かないというルールを徹底しています。

```python
# コンテキストビルダーの概念実装
class ContextBuilder:
    def __init__(self, tenant_id: str, user_id: str, session_id: str):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.session_id = session_id

    def build(self) -> list[dict]:
        messages = []

        # スコープ順に積み上げる（Global → Tenant → User → Session）
        messages.append(self._load_global_policy())
        messages.append(self._load_tenant_context(self.tenant_id))
        messages.append(self._load_user_context(self.user_id))
        messages.extend(self._load_session_history(self.session_id))

        return messages

    def _load_global_policy(self) -> dict:
        # 全テナント共通のポリシーを返す
        return {"role": "system", "content": GLOBAL_POLICY}

    def _load_tenant_context(self, tenant_id: str) -> dict:
        # テナントIDで完全に分離されたコンテキストを返す
        policy = db.get_tenant_policy(tenant_id)
        facts = vector_store.search(tenant_id=tenant_id, query=...)
        return {"role": "system", "content": f"{policy}\n\n{facts}"}
```

---

## 原則2：決定論的リプレイ（Deterministic Replay）

### 問題

エージェントが途中でエラーを起こしたとき、「なぜその判断をしたのか」を追跡できないと、デバッグが不可能になります。また、同じ状況を再現できなければ品質保証もできません。

### 解決策：コンテキストエンジンループ

論文では、エージェントのコンテキスト構築を10ステップのループとして定義しています：

```
1. Retrieve     → 関連メモリの検索
2. Plan         → 取得すべき情報の計画
3. Search       → 外部ソース（RAG/API）からの検索
4. Assemble     → コンテキストの組み立て
5. Stabilize    → コンテキストの正規化・クリーニング
6. Compress     → 要約・圧縮（トークン上限対策）
7. Reason       → LLMによる推論
8. Promote      → 重要な情報をより永続的なメモリへ昇格
9. Trace        → 実行ログの記録
10. Lifecycle   → メモリの有効期限管理
```

**重要なのはStep 9のTrace**です。各ステップで使用したコンテキストをそのまま記録しておくことで、後から完全に再現できます。

```python
import json
import hashlib
from datetime import datetime

class ContextTrace:
    """コンテキスト構築の各ステップを記録するトレーサー"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.steps: list[dict] = []

    def record_step(self, step_name: str, input_data: dict, output_data: dict):
        """各ステップの入出力を記録"""
        self.steps.append({
            "step": step_name,
            "timestamp": datetime.utcnow().isoformat(),
            "input_hash": hashlib.sha256(
                json.dumps(input_data, sort_keys=True).encode()
            ).hexdigest(),
            "output_hash": hashlib.sha256(
                json.dumps(output_data, sort_keys=True).encode()
            ).hexdigest(),
            "input": input_data,
            "output": output_data,
        })

    def save(self, trace_store: "TraceStore"):
        trace_store.write(self.session_id, self.steps)

    def replay(self, step_index: int) -> dict:
        """特定ステップの入力を返す（デバッグ用）"""
        return self.steps[step_index]["input"]
```

### 個人開発での実装

自分のワークフローでは、Obsidianのセッション記録ファイルがこのトレースの役割を担っています。

```markdown
<!-- 06_sessions/2026/02/2026-02-24-1200-xxx.md -->

## コンテキスト使用状況
- CLAUDE.md: v2.1（Global/Policy）
- MEMORY.md: 2026-02-24時点（Tenant/Episodic）
- 参照ファイル: research/xxx.md, src/api/main.py

## 判断ログ
- 10:32 - freee API の wallet_txns で rule_matched フィルタ追加を決定
  - 根拠: MEMORY.md に記載の「安全側に倒す設計」原則
  - 代替案: deal_id のみでフィルタ（却下理由: 自動仕訳が含まれるリスク）
```

このフォーマットにより、後から「なぜその判断をしたのか」を完全に再現できます。

---

## 原則3：経済的予測可能性（Economic Predictability）

### 問題

コンテキストウィンドウに詰め込めるものを全部詰め込むと、トークンコストが指数関数的に増加します。会話が長くなるにつれて毎回のAPIコールが高額になり、商用サービスとして成立しなくなります。

### 解決策：真実ストアと加速層の分離

論文が提案する核心的な設計思想がこれです：

**真実ストア（Source of Truth Store）**
- 永続的なデータベース（RDB、ベクトルDB等）
- 全情報を保持するが、直接LLMに渡さない
- 遅い・安価

**加速層（Acceleration Layer）**
- キャッシュ・要約・インデックス
- LLMに実際に渡す情報のみ格納
- 速い・高価だが最小化する

```python
from typing import Optional
import tiktoken

class ContextAssembler:
    """コンテキストを予算内に収めるアセンブラー"""

    MAX_TOKENS = 8000  # コンテキストウィンドウの上限（余裕を持って設定）
    ENCODING = tiktoken.get_encoding("cl100k_base")

    def __init__(self, truth_store: "TruthStore", cache: "ContextCache"):
        self.truth_store = truth_store
        self.cache = cache

    def count_tokens(self, text: str) -> int:
        return len(self.ENCODING.encode(text))

    def assemble(
        self,
        query: str,
        tenant_id: str,
        budget: int = MAX_TOKENS
    ) -> str:
        remaining = budget
        parts: list[str] = []

        # 優先度順に追加（バジェットを超えたら打ち切り）
        candidates = [
            ("policy", self.cache.get_policy(tenant_id)),
            ("recent_facts", self.cache.get_recent(tenant_id, limit=5)),
            ("relevant_facts", self.truth_store.search(tenant_id, query, limit=10)),
        ]

        for name, content in candidates:
            tokens = self.count_tokens(content)
            if tokens <= remaining:
                parts.append(content)
                remaining -= tokens
            else:
                # バジェット超過時は要約版を使う
                summary = self._summarize(content, remaining)
                if summary:
                    parts.append(summary)
                    remaining -= self.count_tokens(summary)
                break

        return "\n\n".join(parts)

    def _summarize(self, content: str, max_tokens: int) -> Optional[str]:
        """要約APIを呼んでトークン数を削減する"""
        # 実装省略：LLMやextractlibを使って要約
        ...
```

---

## メモリのライフサイクル管理

論文のStep 10（Lifecycle）は、**情報に有効期限を設ける**という考え方です。

すべての情報が永遠に重要なわけではありません。セッション内の一時的な状態は会話終了時に削除、ユーザーの好みは30日でリフレッシュ、など。

```python
from enum import Enum
from datetime import datetime, timedelta

class MemoryTTL(Enum):
    SESSION = timedelta(hours=2)
    USER_PREFERENCE = timedelta(days=30)
    TENANT_FACT = timedelta(days=90)
    GLOBAL_POLICY = None  # 無期限

class MemoryRecord:
    def __init__(
        self,
        content: str,
        scope: str,
        memory_type: str,
        ttl: MemoryTTL,
    ):
        self.content = content
        self.scope = scope
        self.memory_type = memory_type
        self.created_at = datetime.utcnow()
        self.expires_at = (
            self.created_at + ttl.value
            if ttl.value is not None
            else None
        )

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
```

自分のObsidianでは、セッション記録は永続保管ですが、「現在有効なコンテキスト」として `MEMORY.md` に転記する情報は定期的に見直しています。3ヶ月以上参照されていない項目は削除またはアーカイブするというルールを設けています。

---

## 個人開発規模での取捨選択

論文の設計はエンタープライズ向けですが、個人・小規模開発では「やりすぎない」ことも重要です。

| 機能 | エンタープライズ | 個人開発 |
|------|----------------|---------|
| スコープ分離 | DB-level隔離 | ディレクトリ分離で十分 |
| トレース | 全ステップ記録 | セッションMDに重要判断のみ |
| バジェット管理 | リアルタイム計算 | 月次コストチェックで十分 |
| TTL管理 | 自動期限切れ | 定期的な手動アーカイブ |

「真実ストアと加速層の分離」だけは規模に関係なく重要です。**「コンテキストに何でも突っ込む」設計は必ずスケールの壁にぶつかります。**

---

## まとめ

コンテキストエンジニアリングの3原則をまとめます：

1. **構造的隔離** — メモリをスコープ（Global/Tenant/User/Session）×タイプ（Policy/Factual/Episodic）で分類し、混在を防ぐ
2. **決定論的リプレイ** — コンテキスト構築の各ステップをトレースし、後から再現できるようにする
3. **経済的予測可能性** — 真実ストアと加速層を分離し、LLMに渡す情報を予算内に収める

個人開発の規模感では全部を実装する必要はありませんが、この3つの軸で「自分のエージェントはどこが弱いか」を評価する枠組みとして活用できます。

自分自身のワークフローを振り返ると、スコープ分離と決定論的トレースはそれなりにできていますが、トークンバジェット管理は直感に頼っている部分が多く、改善余地があると感じました。

参考文献：[Context Engineering for Commercial Agent Systems](https://jeremydaly.com/context-engineering-for-commercial-agent-systems/)

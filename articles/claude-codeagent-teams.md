---
title: "Claude Code Agent Teamsで複数タスクを並列化する実践ガイド ─ 設計から運用まで"
emoji: "⚡"
type: "tech"
topics: ["claude", "aiagent", "automation", "python", "productivity"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに ─ 「一人のClaudeで足りない」と感じたとき

Claude Codeを使い込んでいくと、必ずある限界にぶつかります。

「このリポジトリ、ファイルが多すぎて1セッションでレビューしきれない」  
「マイクロサービスが5つあるのに、ドキュメント化を直列でやると半日かかる」  
「テスト生成中に別の開発タスクもやりたいのに、待ち続けなければならない」

これらはすべて、**単一エージェントの限界**に由来する問題です。コンテキストウィンドウには上限があり、処理は本質的に逐次的です。1人の優秀なエンジニアに全部頼っているような状態です。

そこで登場するのが **Agent Teams**。複数のClaude Codeインスタンスがオーケストレーターの指揮のもとで並列動作し、タスクを分担処理する仕組みです。

本記事では、実際にAgent Teamsを運用した経験から、**「概念の説明」より「設計判断の基準」**を重視した実践ガイドをお届けします。

:::message
この記事の対象: Claude Codeを3〜6ヶ月程度使っており、基本操作に慣れている方。Agent Teamsの入門よりも「どう設計すべきか」を知りたい方向けです。
:::

---

## Agent Teamsの全体像 ─ オーケストレーターとワーカーの役割分担

### アーキテクチャを一目で理解する

Agent Teamsの構造はシンプルです。**オーケストレーター（指揮者）** が全体を管理し、**サブエージェント（ワーカー）** が個別タスクを担当します。

```
┌──────────────────────────────────────┐
│         Orchestrator Agent            │
│                                       │
│  ┌─────────────┐  ┌───────────────┐  │
│  │ タスク分解   │→ │  結果を集約   │  │
│  └─────────────┘  └───────────────┘  │
└──────┬──────────────────┬────────────┘
       │                  │
  ┌────▼────┐        ┌────▼────┐
  │Worker A │        │Worker B │
  │(独立)   │        │(独立)   │
  └─────────┘        └─────────┘
```

**重要な原則は「コンテキスト分離」です。**

各サブエージェントは独立したコンテキストを持ちます。Worker AはWorker Bの作業内容を知りません。この分離こそが、並列化を可能にすると同時に、設計上の注意点を生み出します。

### 2つの実行モデル

Agent Teamsには2つの基本モデルがあります。

| モデル | 構造 | 向くケース |
|--------|------|-----------|
| **並列モデル** | 複数ワーカーが同時に異なるタスクを処理 | 独立したファイル群のレビュー、並列テスト生成 |
| **パイプラインモデル** | タスクAの出力がタスクBの入力になる直列チェーン | 翻訳→校正→フォーマット変換 |

多くのユースケースはこの2つの組み合わせです。「フェーズ1を並列処理し、結果を集約してからフェーズ2に渡す」という Fork-Join パターンが典型です。

### なぜコンテキスト分離が重要なのか

サブエージェントに不必要な情報を渡すと、以下の問題が起きます。

1. **トークン消費の増大**: 使わない情報まで読み込む
2. **処理品質の低下**: 無関係な文脈が判断を歪める
3. **コンテキスト上限への早期到達**: 結果的に処理できる量が減る

「各ワーカーに必要最小限の情報だけを渡す」。これがAgent Teams設計の根幹です。

---

## 実装ステップ ─ 最初のAgent Teamを動かす

### Step 1: タスク分解の設計（最重要工程）

「実装より設計に時間をかける」。これがAgent Teams運用で最も学んだ教訓です。

**タスク分解で必ず確認すること:**

```
[ ] 各タスクは独立しているか？（他のタスクの完了を待たなくていいか）
[ ] 出力フォーマットは統一されているか？（集約しやすい形か）
[ ] タスクの粒度は適切か？（細かすぎてオーバーヘッドが大きくないか）
[ ] 冪等性があるか？（失敗時に安全に再実行できるか）
```

**悪いタスク分解の例:**

```
❌ Worker A: ファイルAを解析して問題点を抽出する
   Worker B: Worker Aの結果を参考にファイルBを解析する  ← 依存関係あり、並列不可
```

**良いタスク分解の例:**

```
✅ Worker A: ファイルA群（1-50）を独立してレビューし、JSON形式で出力
   Worker B: ファイルB群（51-100）を独立してレビューし、JSON形式で出力
   → Orchestratorが集約して最終レポートを生成
```

### Step 2: オーケストレーターのプロンプト設計

オーケストレーターには「何を」「誰に」「どう分割して」「どう集約するか」を明示します。

```python
ORCHESTRATOR_PROMPT = """
あなたはコードレビューチームのマネージャーです。
以下のファイルリストを {n_workers} グループに均等分割し、
各グループを並列でレビューするサブエージェントに割り当ててください。

## 重要な制約
- 各サブエージェントは完全に独立して動作します
- 他のサブエージェントの結果を参照してはいけません
- 全てのサブエージェントが完了してから結果を集約してください

## 対象ファイル
{file_list}

## 各サブエージェントへの指示
以下のフォーマットで問題点を報告してください:

```json
{{
  "file": "ファイルパス",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "line": 行番号,
      "description": "問題の説明"
    }}
  ]
}}
```

## 集約ルール
全サブエージェントの結果を受け取ったら:
1. severity: HIGH の問題を優先してリスト化
2. ファイル別にグループ化
3. 修正コスト（推定時間）を付記する
"""
```

### Step 3: Task toolで並列サブエージェントを起動する

Claude CodeのTask toolを使って、サブエージェントを並列起動します。

```python
import subprocess
import json
import concurrent.futures
from pathlib import Path

def create_worker_prompt(files: list[str], worker_id: int) -> str:
    """各ワーカー用プロンプトを生成"""
    file_list = "\n".join(f"- {f}" for f in files)
    return f"""
あなたはコードレビューの専門家です（Worker {worker_id}）。
以下のファイルをレビューし、問題点をJSON形式で報告してください。

## 対象ファイル
{file_list}

## 出力形式
各ファイルについて以下のJSONを出力:
{{"file": "path", "issues": [{{"severity": "HIGH|MEDIUM|LOW", "line": N, "description": "説明"}}]}}
"""

def run_agent(prompt: str, output_path: Path) -> dict:
    """サブエージェントを起動して結果を返す"""
    result = subprocess.run(
        ["claude", "--print", prompt],
        capture_output=True,
        text=True,
        timeout=300  # 5分タイムアウト
    )
    
    if result.returncode != 0:
        return {"error": result.stderr, "output_path": str(output_path)}
    
    # 結果をファイルに保存（冪等性のため）
    output_path.write_text(result.stdout)
    return {"success": True, "output": result.stdout}

def parallel_code_review(repo_path: str, n_workers: int = 4) -> list[dict]:
    """並列コードレビューを実行"""
    # レビュー対象ファイルを収集
    py_files = list(Path(repo_path).rglob("*.py"))
    
    # ファイルをn_workersグループに分割
    chunks = [py_files[i::n_workers] for i in range(n_workers)]
    
    # 出力ディレクトリを準備
    output_dir = Path("/tmp/review_results")
    output_dir.mkdir(exist_ok=True)
    
    # 並列実行
    tasks = []
    for i, chunk in enumerate(chunks):
        if not chunk:
            continue
        prompt = create_worker_prompt([str(f) for f in chunk], i)
        output_path = output_dir / f"worker_{i}.json"
        tasks.append((prompt, output_path))
    
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(run_agent, prompt, path): i
            for i, (prompt, path) in enumerate(tasks)
        }
        for future in concurrent.futures.as_completed(futures):
            worker_id = futures[future]
            try:
                result = future.result()
                results.append({"worker_id": worker_id, **result})
            except Exception as e:
                results.append({"worker_id": worker_id, "error": str(e)})
    
    return results
```

### Step 4: 出力の集約と後処理

サブエージェントの出力を集約するオーケストレーターロジック:

```python
def aggregate_results(results: list[dict]) -> dict:
    """全ワーカーの結果を集約"""
    all_issues = []
    errors = []
    
    for result in results:
        if "error" in result:
            errors.append(result)
            continue
        
        # JSONパース（エラーハンドリング込み）
        try:
            output = result.get("output", "")
            # JSONブロックを抽出
            import re
            json_blocks = re.findall(r'\{.*?\}', output, re.DOTALL)
            for block in json_blocks:
                issue_data = json.loads(block)
                all_issues.append(issue_data)
        except json.JSONDecodeError:
            # JSON以外の出力は別途処理
            pass
    
    # severity別にソート
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_issues.sort(
        key=lambda x: severity_order.get(
            x.get("severity", "LOW"), 2
        )
    )
    
    return {
        "total_issues": len(all_issues),
        "high_severity": [i for i in all_issues if i.get("severity") == "HIGH"],
        "medium_severity": [i for i in all_issues if i.get("severity") == "MEDIUM"],
        "low_severity": [i for i in all_issues if i.get("severity") == "LOW"],
        "worker_errors": errors,
    }
```

---

## 並列化が「効く」タスクと「効かない」タスク

最も重要な判断は「このタスクを並列化すべきか」です。闇雲に並列化するとコストが増えるだけで、効果が出ません。

### 並列化に向くタスクの3条件

```
条件1: 独立性
  → 各タスクが他のタスクの結果に依存していない
  → 例: ファイル単位のレビュー、モジュール単位のテスト生成

条件2: 均質性
  → タスクの性質・難度が揃っている（偏りがないと負荷分散しやすい）
  → 例: 同じ言語の複数ファイル処理

条件3: 出力の結合可能性
  → 個別出力を機械的に合成できる
  → 例: JSON配列、Markdown見出し別セクション
```

### 向き・不向きマトリクス

| タスク種別 | 並列化 | 理由 |
|-----------|--------|------|
| 大規模リポジトリのコードレビュー | ✅ 向く | ファイル間の独立性が高い |
| モジュール別テスト生成 | ✅ 向く | 各モジュールが独立 |
| 多言語ドキュメント翻訳 | ✅ 向く | 言語間の依存なし |
| CSVデータのバリデーション | ✅ 向く | 行単位で独立 |
| アーキテクチャ設計 | ❌ 向かない | 全体最適化が必要 |
| バグ修正（文脈依存） | ❌ 向かない | 原因の特定には全体把握が必要 |
| リファクタリング（横断的） | ⚠️ 要注意 | 変更が他ファイルに波及する可能性あり |
| データベースマイグレーション | ❌ 向かない | 順序依存・排他制御が必要 |

### アンチパターン集

**アンチパターン1: 依存関係のあるタスクを並列化する**

```
❌ Worker A: schema.py を解析してデータ構造を把握する
   Worker B: Worker Aの結果を使ってAPIエンドポイントを実装する
   → BはAの完了を待つ必要があるため並列不可
```

**アンチパターン2: 出力が大きすぎるサブタスクの設定**

```
❌ Worker A: 500ファイルを一度にレビューする
   → コンテキスト上限に達して途中で止まる
   → 適切な粒度: 1ワーカーあたり20〜50ファイル程度
```

**アンチパターン3: 共有リソースへの同時書き込み**

```
❌ 全ワーカーが同じDBレコードを更新する
   → 競合が発生して結果が不整合になる
   → 解決: ワーカーごとに別のファイル/テーブルに書き出し、後で集約
```

---

## 運用で必ず直面する課題と対処法

### コストとトークンの現実的な管理

並列化は**コストも並列化します**。この点は強調してもしすぎることはありません。

```
例: ファイル100件のレビュー

直列処理:
  使用トークン: 50,000 tokens × 1 = 50,000 tokens
  実行時間: 20分

並列処理（4ワーカー）:
  使用トークン: 15,000 tokens × 4 = 60,000 tokens  ← 約20%増
  実行時間: 7分  ← 65%削減
```

トークンが増える主な理由は、各ワーカーが「システムプロンプト」「コンテキスト初期化」をそれぞれ消費するためです。

**コスト管理のベストプラクティス:**

```python
# 実行前にコスト見積もりを出す
def estimate_cost(n_files: int, n_workers: int, avg_tokens_per_file: int = 500) -> dict:
    total_tokens = n_files * avg_tokens_per_file
    overhead_per_worker = 2000  # システムプロンプト等のオーバーヘッド
    
    serial_cost = total_tokens
    parallel_cost = total_tokens + (overhead_per_worker * n_workers)
    
    return {
        "serial_tokens": serial_cost,
        "parallel_tokens": parallel_cost,
        "overhead_ratio": parallel_cost / serial_cost,
        "time_reduction_estimate": f"約{int((1 - 1/n_workers) * 100)}%削減"
    }

# 使用例
estimate = estimate_cost(n_files=100, n_workers=4)
print(f"オーバーヘッド比: {estimate['overhead_ratio']:.2f}x")
# → オーバーヘッド比: 1.16x（16%増）
```

**判断基準**: オーバーヘッド比が1.5倍を超えるならワーカー数を減らすか直列処理を検討します。

### エラーが起きたとき: 部分失敗への対応設計

4ワーカーを並列実行して1つが失敗したとき、どう処理するか。**設計段階でこの「部分失敗」を想定しておく**ことが運用安定性の鍵です。

```python
class AgentTeamRunner:
    def __init__(self, retry_limit: int = 2):
        self.retry_limit = retry_limit
        self.results = {}
        self.failures = {}
    
    def run_with_retry(self, worker_id: str, prompt: str) -> dict:
        """リトライ付きワーカー実行"""
        for attempt in range(self.retry_limit + 1):
            try:
                result = run_agent(prompt, Path(f"/tmp/worker_{worker_id}.json"))
                if "error" not in result:
                    self.results[worker_id] = result
                    return result
            except Exception as e:
                if attempt == self.retry_limit:
                    self.failures[worker_id] = str(e)
                    return {"error": str(e), "worker_id": worker_id}
        
        return {"error": "max retries exceeded", "worker_id": worker_id}
    
    def get_partial_results(self) -> dict:
        """部分失敗時も成功分で進める"""
        success_rate = len(self.results) / (len(self.results) + len(self.failures))
        
        if success_rate < 0.5:
            raise RuntimeError(f"成功率が50%未満: {success_rate:.0%}")
        
        return {
            "results": self.results,
            "failures": self.failures,
            "success_rate": success_rate,
            "can_proceed": success_rate >= 0.8  # 80%以上で続行
        }
```

### 冪等性の確保: 安全に再実行できる設計

ワーカーが途中で失敗したとき、「どこまで完了したか」を把握できる設計が重要です。

```python
from pathlib import Path
import hashlib

def get_task_cache_key(files: list[str]) -> str:
    """タスクの同一性を確認するキャッシュキー"""
    content = "".join(sorted(files))
    return hashlib.md5(content.encode()).hexdigest()[:8]

def run_with_cache(files: list[str], worker_id: int) -> dict:
    """キャッシュ済みの結果があればスキップ"""
    cache_key = get_task_cache_key(files)
    cache_path = Path(f"/tmp/review_cache/{cache_key}_worker{worker_id}.json")
    
    # キャッシュがあれば読み込んで返す
    if cache_path.exists():
        print(f"Worker {worker_id}: キャッシュから復元 ({cache_key})")
        return json.loads(cache_path.read_text())
    
    # 実行して結果をキャッシュ
    cache_path.parent.mkdir(exist_ok=True)
    result = run_agent(create_worker_prompt(files, worker_id), cache_path)
    return result
```

### 進捗の可視化

複数ワーカーが動いているとき、「何が完了していて何が動いているか」をリアルタイムで把握する仕組みを用意します。

```python
import threading
import time

class ProgressTracker:
    def __init__(self, total_workers: int):
        self.total = total_workers
        self.completed = 0
        self.lock = threading.Lock()
        self.start_time = time.time()
    
    def mark_complete(self, worker_id: str, success: bool):
        with self.lock:
            self.completed += 1
            elapsed = time.time() - self.start_time
            status = "✅" if success else "❌"
            print(f"{status} Worker {worker_id} 完了 "
                  f"({self.completed}/{self.total}) "
                  f"経過: {elapsed:.0f}秒")
    
    def print_summary(self):
        elapsed = time.time() - self.start_time
        print(f"\n完了: {self.completed}/{self.total}ワーカー, "
              f"合計時間: {elapsed:.0f}秒")
```

---

## 実践ユースケース集 ─ すぐに使える3パターン

### パターン1: 大規模リポジトリのテスト自動生成

モジュール単位でテストを並列生成し、カバレッジを短時間で引き上げます。

```python
TEST_GENERATION_PROMPT = """
以下のPythonモジュールに対するユニットテストを生成してください。

## モジュール: {module_path}
```python
{module_code}
```

## 制約
- pytestを使用する
- 正常系・異常系・エッジケースを網羅する
- モックは unittest.mock を使用する
- テストファイルを {test_path} に保存可能な形式で出力する

## 出力形式
テストコードのみを出力してください（説明文不要）。
"""

def generate_tests_parallel(src_dir: str, n_workers: int = 3):
    modules = list(Path(src_dir).rglob("*.py"))
    modules = [m for m in modules if not m.name.startswith("test_")]
    
    chunks = [modules[i::n_workers] for i in range(n_workers)]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = []
        for i, chunk in enumerate(chunks):
            for module_path in chunk:
                module_code = module_path.read_text()
                test_path = module_path.parent / f"test_{module_path.name}"
                
                prompt = TEST_GENERATION_PROMPT.format(
                    module_path=str(module_path),
                    module_code=module_code,
                    test_path=str(test_path)
                )
                futures.append(executor.submit(run_agent, prompt, test_path))
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            print(f"テスト生成完了: {result}")
```

### パターン2: 複数マイクロサービスの一括ドキュメント化

サービスごとに独立したドキュメントを並列生成します。

```
# オーケストレーターへの指示例（Claude Codeに直接入力）

以下の5つのマイクロサービスについて、それぞれ独立した
README.mdを並列で生成してください。

各サービスのAPIエンドポイント、依存関係、起動手順を含めること。
出力は各ディレクトリのREADME.mdとして保存すること。

サービス一覧:
- services/auth/
- services/payment/
- services/notification/
- services/user/
- services/analytics/

【重要】各ワーカーは他のサービスのドキュメントを参照せずに
独立してドキュメントを作成してください。
```

### パターン3: CSVデータ変換・バリデーションの並列処理

大量のCSVファイルを分割してバリデーションと変換を並列実行します。

```python
VALIDATION_PROMPT = """
以下のCSVデータをバリデーションし、問題があれば報告してください。

## データ（{row_count}行）
{csv_sample}

## バリデーションルール
1. email列: 正しいメール形式であること
2. phone列: 数字のみ、10〜11桁であること
3. created_at列: ISO 8601形式であること
4. 必須列（name, email）がNullでないこと

## 出力形式
```json
{
  "valid_rows": 行数,
  "invalid_rows": [{"row": 行番号, "column": 列名, "issue": "説明"}],
  "summary": "全体サマリー"
}
```
"""

def validate_csv_parallel(csv_path: str, chunk_size: int = 1000):
    import pandas as pd
    
    df = pd.read_csv(csv_path)
    chunks = [df[i:i+chunk_size] for i in range(0, len(df), chunk_size)]
    
    print(f"処理開始: {len(df)}行 → {len(chunks)}チャンク")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for i, chunk in enumerate(chunks):
            prompt = VALIDATION_PROMPT.format(
                row_count=len(chunk),
                csv_sample=chunk.to_csv(index=False)[:3000]  # サンプル
            )
            output_path = Path(f"/tmp/validation_{i}.json")
            futures.append(executor.submit(run_agent, prompt, output_path))
        
        all_results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    return aggregate_results(all_results)
```

---

## まとめ ─ Agent Teamsで広がる自動化の地平

本記事で学んだ核心を3点にまとめます。

**1. タスク分解が全て**  
並列化の成否は実装コードよりも「タスクを独立した単位に分解できるか」で決まります。依存関係のあるタスクを並列化しようとしても意味がありません。

**2. コストを意識した設計**  
並列化はトークン消費も並列化します。オーバーヘッド比が1.5倍を超えるなら、並列化のメリットが薄れます。「何ワーカーが最適か」を見積もってから実行してください。

**3. 部分失敗を前提とした設計**  
4ワーカーが全て成功する保証はありません。冪等性とリトライ機構を最初から設計に組み込むことで、運用時の安定性が大きく変わります。

Agent Teamsはまだ進化中の機能です。「試して壊す」サイクルを短くしながら、自分のユースケースに合った設計を見つけていってください。

:::message
次のステップとして、Agent Teamsにデビルズアドボケイト（批判的レビュアー）を組み込む方法を別記事で解説しています。品質ゲートとしてのエージェント設計に興味のある方はそちらも参考にしてみてください。
:::

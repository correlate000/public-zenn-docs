---
title: "FastAPIでMCPサーバーをゼロから作る──Claude Codeに自分だけのツールを繋ぐ実践ガイド"
emoji: "🔌"
type: "tech"
topics: ["mcp", "fastapi", "claudecode", "python", "api"]
published: true
status: "published"
publication_name: "correlate_dev"
---

「Claude Codeが賢いのはわかった。でも、自分のローカルDBは参照できないし、社内ツールには繋がらない。」── MCP（Model Context Protocol）はその壁を壊すプロトコルです。FastAPIを使えば、Pythonだけで自分専用のツールサーバーを立てられます。この記事では、概念説明を最小限に抑え、 ** 実際に動かすことを最優先 ** に全工程を解説する。

## そもそもMCPとは？── 30秒で理解するプロトコルの本質

### Claude CodeとMCPの関係

MCPのアーキテクチャはシンプルです。

```
Claude Code（MCP Client）
    ↕ JSON-RPC 2.0
MCP Server（FastAPIで実装）
    ↕ 任意の手段
外部ツール・DB・API
```

Claude CodeはMCP Clientとして動作し、登録されたMCP Serverのツールを必要に応じて呼び出します。MCPには3つの概念があります。

| 概念 | 役割 | 例 |
|------|------|-----|
| Tools | Claudeが能動的に呼び出す関数 | DB検索、API呼び出し |
| Resources | Claudeが参照できるデータソース | ファイル、URLコンテンツ |
| Prompts | 再利用可能なプロンプトテンプレート | コードレビュー雛形 |

個人開発で最もよく使うのは **Tools** です。本記事もToolsに絞って解説します。

### Transport層：stdioかHTTPか

MCPには接続方式が複数あります。

| 方式 | 特徴 | 推奨ケース |
|------|------|-----------|
| stdio | プロセス間通信、設定が簡単 | ローカル専用ツール |
| HTTP + SSE | HTTPサーバー、デバッグしやすい | 開発・学習用途 |
| WebSocket | 双方向通信、リアルタイム性高い | 高度なユースケース |

FastAPIを使う場合は **HTTP + SSE** が最も相性が良く、デバッグもしやすいためこちらを採用します。

### JSON-RPC 2.0を怖がらない

MCPの通信はJSON-RPC 2.0ベースです。実際のパケットを見ると難しくありません。

```json
// Claudeがツールを呼び出すリクエスト
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "search_notes",
    "arguments": { "query": "FastAPI" }
  },
  "id": 1
}

// サーバーからのレスポンス
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "検索結果: 3件のノートが見つかりました..."
      }
    ]
  },
  "id": 1
}
```

FastAPIはPydanticとの組み合わせでスキーマバリデーションを自動化できるため、このJSON構造を扱うのに非常に向いています。

---

## 環境構築── ゼロから始める最小構成

### 必要なものを揃える

Python 3.11以上を使います（`match`文を活用するため）。パッケージ管理には `uv` を推奨します。

```bash
# プロジェクト作成
uv init mcp-fastapi-server
cd mcp-fastapi-server

# 依存パッケージのインストール
uv add fastapi uvicorn pydantic python-dotenv httpx
```

ディレクトリ構成はこのようにします。

```
mcp-fastapi-server/
├── main.py              # FastAPIアプリ本体・ルーティング
├── schemas.py           # Pydanticスキーマ定義
├── tools/               # ツール定義モジュール
│   ├── __init__.py
│   ├── sqlite_search.py # SQLite検索ツール
│   └── note_search.py   # ノート検索ツール
├── .env                 # 環境変数（Git管理外）
└── pyproject.toml
```

### MCP Inspectorで接続確認できる準備をする

後でデバッグに使うので、MCP Inspectorも入れておきます。Node.jsが必要です。

```bash
# MCP Inspectorのインストール（グローバル）
npm install -g @modelcontextprotocol/inspector

# または、npxでその場実行
npx @modelcontextprotocol/inspector
```

---

## FastAPIでMCPサーバーを実装する── コア実装完全解説

### Step 1: Pydanticスキーマを定義する

まず、MCPのリクエスト・レスポンス形式をPydanticで定義します。

```python
# schemas.py
from pydantic import BaseModel
from typing import Any, Optional

class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[dict[str, Any]] = None
    id: int | str | None = None

class ToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any]

class TextContent(BaseModel):
    type: str = "text"
    text: str

class ToolResult(BaseModel):
    content: list[TextContent]
    isError: bool = False
```

### Step 2: MCPサーバーのコアを実装する

```python
# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from schemas import MCPRequest, ToolDefinition, TextContent, ToolResult
from tools import TOOL_REGISTRY  # 後で定義

app = FastAPI(title="My MCP Server", version="0.1.0")

# ローカル開発時のCORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def success_response(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": id}

def error_response(id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": id,
    }

def handle_initialize(id: Any) -> dict:
    return success_response(id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "my-mcp-server", "version": "0.1.0"},
    })

def handle_tools_list(id: Any) -> dict:
    tools = [tool["definition"] for tool in TOOL_REGISTRY.values()]
    return success_response(id, {"tools": tools})

async def handle_tools_call(request: MCPRequest) -> dict:
    params = request.params or {}
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if tool_name not in TOOL_REGISTRY:
        return error_response(request.id, -32602, f"Tool '{tool_name}' not found")

    try:
        result_text = await TOOL_REGISTRY[tool_name]["handler"](arguments)
        return success_response(request.id, {
            "content": [{"type": "text", "text": result_text}]
        })
    except Exception as e:
        return success_response(request.id, {
            "content": [{"type": "text", "text": f"エラー: {str(e)}"}],
            "isError": True,
        })

@app.post("/")
async def mcp_handler(request: MCPRequest) -> dict:
    match request.method:
        case "initialize":
            return handle_initialize(request.id)
        case "initialized":
            return success_response(request.id, {})
        case "tools/list":
            return handle_tools_list(request.id)
        case "tools/call":
            return await handle_tools_call(request)
        case "ping":
            return success_response(request.id, {})
        case _:
            return error_response(request.id, -32601, f"Method not found: {request.method}")

@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

### Step 3: ツールレジストリを作る

ツールを追加・管理しやすくするためのレジストリパターンを使います。

```python
# tools/__init__.py
from tools.sqlite_search import TOOL_DEFINITION as SQLITE_DEF, execute as sqlite_execute
from tools.note_search import TOOL_DEFINITION as NOTE_DEF, execute as note_execute

# ツール名 → 定義・ハンドラのマッピング
TOOL_REGISTRY = {
    "search_local_db": {
        "definition": SQLITE_DEF,
        "handler": sqlite_execute,
    },
    "search_notes": {
        "definition": NOTE_DEF,
        "handler": note_execute,
    },
}
```

### Step 4: 最初のツールを実装する── ローカルSQLite検索

```python
# tools/sqlite_search.py
import sqlite3
import json
from pathlib import Path
from typing import Any

TOOL_DEFINITION = {
    "name": "search_local_db",
    "description": "ローカルのSQLiteデータベースからレコードを全文検索します。テーブル名とキーワードを指定してください。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "table": {
                "type": "string",
                "description": "検索対象のテーブル名",
            },
            "keyword": {
                "type": "string",
                "description": "検索キーワード（部分一致）",
            },
            "limit": {
                "type": "integer",
                "description": "取得件数の上限（デフォルト: 10）",
                "default": 10,
            },
        },
        "required": ["table", "keyword"],
    },
}

async def execute(args: dict[str, Any]) -> str:
    table = args["table"]
    keyword = args["keyword"]
    limit = args.get("limit", 10)

    # テーブル名はホワイトリスト方式でバリデーション（SQLインジェクション対策）
    allowed_tables = {"tasks", "notes", "projects"}
    if table not in allowed_tables:
        return f"エラー: テーブル '{table}' は許可されていません。許可テーブル: {', '.join(allowed_tables)}"

    db_path = Path("./data/local.db")
    if not db_path.exists():
        return "エラー: データベースファイルが見つかりません"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE CAST(* AS TEXT) LIKE ? LIMIT ?",  # noqa
            (f"%{keyword}%", limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            return f"'{keyword}' に一致するレコードは見つかりませんでした"
        return json.dumps(rows, ensure_ascii=False, indent=2)
    finally:
        conn.close()
```

:::message
SQLiteの全文検索では `FTS5`（Full-Text Search）拡張を使うとより高速・高精度になります。本記事ではシンプルさを優先して `LIKE` 検索にしています。
:::

---

## Claude Codeに接続する── 設定ファイルの完全解説

### claude_desktop_config.json の場所と構造

Claude Codeの設定ファイルはOSによって場所が異なります。

| OS | パス |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

設定ファイルの書き方はこのようになります。

```json
{
  "mcpServers": {
    "my-personal-api": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/mcp-fastapi-server",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8888"
      ],
      "env": {
        "DATABASE_URL": "sqlite:///./data/local.db"
      }
    }
  }
}
```

:::message alert
`/path/to/mcp-fastapi-server` は実際の絶対パスに置き換えてください。`~` は展開されないため、`/Users/yourname/...` のように書く必要があります。
:::

### url方式との使い分け

設定方式は2種類あります。

| 方式 | 記述 | 用途 |
|------|------|------|
| command方式 | `"command": "uvicorn"` | Claude起動時に自動でサーバーも起動 |
| url方式 | `"url": "http://127.0.0.1:8888"` | 常時起動サーバーに接続 |

個人開発では `command` 方式が手軽です。Claude Codeを起動するとMCPサーバーも自動で立ち上がります。

### ツールが認識されているか確認する

設定後にClaude Codeを再起動すると、会話の中でツールが使えるようになります。確認方法はこのように試してください。

```
あなた: 登録されているツールを教えてください

Claude: 現在利用可能なツールは以下の通りです：
- search_local_db: ローカルのSQLiteデータベースを全文検索
- search_notes: ノートファイルを検索
```

---

## MCP Inspectorでデバッグする

Claude Codeに繋ぐ前に、MCP Inspectorで単体テストをするのが確実です。

```bash
# サーバーを起動しておく
uv run uvicorn main:app --host 127.0.0.1 --port 8888 --reload

# 別ターミナルでInspectorを起動
npx @modelcontextprotocol/inspector http://127.0.0.1:8888
```

ブラウザで `http://localhost:5173` を開くと、GUIでツール一覧の確認・呼び出しテストができます。

** よくあるハマりポイントと解決策:**

| 症状 | 原因 | 解決策 |
|------|------|--------|
| ツールが表示されない | `tools/list` の実装ミス | `curl -X POST http://127.0.0.1:8888/ -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'` で直接確認 |
| `Method not found` | methodのmatch条件漏れ | `case _:` のフォールバックのログを確認 |
| Claude Codeがサーバーを認識しない | configのパス誤り | パスを絶対パスで書き直す |
| ツール呼び出しがタイムアウト | 処理が重すぎる | 非同期化・キャッシュを検討 |

---

## 実践ユースケース集── すぐ使えるレシピ3選

### レシピ1: Obsidianノートを全文検索するツール

ローカルのMarkdownファイルを `ripgrep` で検索するツールです。

```python
# tools/note_search.py
import asyncio
import json
from pathlib import Path
from typing import Any

OBSIDIAN_VAULT = Path("/Users/yourname/dev/Obsidian")

TOOL_DEFINITION = {
    "name": "search_notes",
    "description": "Obsidianのノートを全文検索します。キーワードを含むノートのタイトルと抜粋を返します。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "検索キーワード（正規表現使用可）",
            },
            "limit": {
                "type": "integer",
                "description": "最大取得件数（デフォルト: 5）",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

async def execute(args: dict[str, Any]) -> str:
    query = args["query"]
    limit = args.get("limit", 5)

    # ripgrepで検索（非同期実行）
    proc = await asyncio.create_subprocess_exec(
        "rg",
        "--json",
        "--max-count", "1",
        "--glob", "*.md",
        query,
        str(OBSIDIAN_VAULT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    results = []
    for line in stdout.decode().strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
            if data.get("type") == "match":
                path = data["data"]["path"]["text"]
                text = data["data"]["lines"]["text"].strip()
                title = Path(path).stem
                results.append(f"## {title}\n> {text}\nパス: {path}")
                if len(results) >= limit:
                    break
        except (json.JSONDecodeError, KeyError):
            continue

    if not results:
        return f"'{query}' に一致するノートは見つかりませんでした"

    return f"{len(results)}件のノートが見つかりました:\n\n" + "\n\n".join(results)
```

### レシピ2: GitHub IssueをサマリするPRレビュー補助ツール

```python
# tools/github_issues.py
import httpx
import os
from typing import Any

TOOL_DEFINITION = {
    "name": "list_github_issues",
    "description": "GitHubリポジトリのオープンなIssueを取得してサマリします",
    "inputSchema": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "リポジトリオーナー名"},
            "repo": {"type": "string", "description": "リポジトリ名"},
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Issueの状態（デフォルト: open）",
                "default": "open",
            },
        },
        "required": ["owner", "repo"],
    },
}

async def execute(args: dict[str, Any]) -> str:
    owner = args["owner"]
    repo = args["repo"]
    state = args.get("state", "open")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return "エラー: GITHUB_TOKEN 環境変数が設定されていません"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            headers=headers,
            params={"state": state, "per_page": 10},
        )
        resp.raise_for_status()
        issues = resp.json()

    if not issues:
        return f"{owner}/{repo} にオープンなIssueはありません"

    lines = [f"## {owner}/{repo} のIssue一覧（{state}）\n"]
    for issue in issues:
        lines.append(
            f"- **#{issue['number']}** {issue['title']}\n"
            f"  ラベル: {', '.join(l['name'] for l in issue['labels']) or 'なし'}\n"
            f"  URL: {issue['html_url']}"
        )

    return "\n".join(lines)
```

環境変数は `.env` で管理します。

```bash
# .env（Gitに含めない）
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
DATABASE_URL=sqlite:///./data/local.db
```

`python-dotenv` を使って読み込みます。

```python
# main.py の先頭に追加
from dotenv import load_dotenv
load_dotenv()
```

### レシピ3: タスク管理ツール（Todoistなど）のラッパー

特定APIのラッパーパターンです。このパターンを応用すれば、Notion、Linear、Jiraなど任意のSaaSと繋げられます。

```python
# tools/todoist_tasks.py
import httpx
import os
from typing import Any

TOOL_DEFINITION = {
    "name": "get_todoist_tasks",
    "description": "Todoistの今日のタスク一覧を取得します",
    "inputSchema": {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Todoistのフィルター構文（例: 'today', 'p1', '#Work'）",
                "default": "today",
            }
        },
        "required": [],
    },
}

async def execute(args: dict[str, Any]) -> str:
    token = os.environ.get("TODOIST_API_TOKEN")
    if not token:
        return "エラー: TODOIST_API_TOKEN が設定されていません"

    filter_str = args.get("filter", "today")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.todoist.com/rest/v2/tasks",
            headers={"Authorization": f"Bearer {token}"},
            params={"filter": filter_str},
        )
        resp.raise_for_status()
        tasks = resp.json()

    if not tasks:
        return f"フィルター '{filter_str}' に一致するタスクはありません"

    lines = [f"## タスク一覧（{filter_str}）\n"]
    for task in tasks:
        priority = "🔴" if task["priority"] == 4 else "🟡" if task["priority"] == 3 else "⚪"
        lines.append(f"{priority} {task['content']} (ID: {task['id']})")

    return "\n".join(lines)
```

---

## セキュリティ── 個人開発でも押さえるべき最低限

### ローカル限定での安全な運用

MCPサーバーをローカルで動かす場合、バインドアドレスを必ず `127.0.0.1` に限定してください。

```bash
# 危険: 全インターフェースにバインド（外部からアクセス可能）
uvicorn main:app --host 0.0.0.0 --port 8888

# 安全: localhostにのみバインド
uvicorn main:app --host 127.0.0.1 --port 8888
```

### APIキー認証の最小実装

外部公開する場合は必ずAPIキー認証を追加します。

```python
# main.py に追加
import os
from fastapi import Header, HTTPException

EXPECTED_API_KEY = os.environ.get("MCP_API_KEY")

async def verify_api_key(x_api_key: str = Header(None)):
    if EXPECTED_API_KEY and x_api_key != EXPECTED_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# エンドポイントにdependencyとして追加
@app.post("/", dependencies=[Depends(verify_api_key)])
async def mcp_handler(request: MCPRequest) -> dict:
    ...
```

Claude Code側の設定でヘッダーを送るには、`env` に設定して `httpx` などでヘッダーに付与する実装が必要です。自前HTTP実装だからこそ、こういった制御が柔軟にできる点がSDK直接利用との差別化になります。

### ngrokで一時的に外部公開する場合

モバイルや別マシンからアクセスしたい場合に `ngrok` を使うことがあります。その際は必ずBasic認証を追加してください。

```bash
# ngrokの設定でBasic認証を追加
ngrok http 8888 --basic-auth="user:strongpassword"
```

---

## まとめ： 作ったものの全体像

本記事で構築したMCPサーバーの全体像を整理します。

```
mcp-fastapi-server/
├── main.py              ✅ JSON-RPCルーティング（initialize/tools/list/tools/call）
├── schemas.py           ✅ Pydanticによる型安全なスキーマ
├── tools/
│   ├── __init__.py      ✅ ツールレジストリ（追加・削除が容易）
│   ├── sqlite_search.py ✅ ローカルDB全文検索
│   ├── note_search.py   ✅ Obsidianノート全文検索（ripgrep）
│   ├── github_issues.py ✅ GitHub Issue取得
│   └── todoist_tasks.py ✅ Todoistタスク取得
└── .env                 ✅ 環境変数（Git管理外）
```

Claude Codeへの接続は `claude_desktop_config.json` に設定するだけです。

### MCPエコシステムの今後

2024〜2025年にかけてMCPは急速に普及しています。公式の `mcp-python-sdk` のアップデートも活発で、Anthropic以外のLLMプロバイダーも対応を進めています。今後の注目ポイントは以下のとおり。

- **OAuth 2.0対応 **: MCP仕様にOAuth認証フローが追加予定
- **Sampling機能 **: MCPサーバー側からLLMを呼び出せる機能
- **Claude.ai Web版での対応 **: デスクトップアプリ限定から解放される可能性

### 発展的なトピックへのポインタ

- `mcp-python-sdk`（公式SDK）を使った実装: プロダクション向けに移行する際の選択肢
- `fastmcp`: FastAPIラッパーをさらに抽象化したサードパーティライブラリ
- SSEストリーミング: 長時間処理のリアルタイム進捗通知への対応

FastAPIでMCPサーバーを自前実装することで、プロトコルの仕組みを深く理解できます。`mcp-python-sdk` を使う際にも、内部で何が起きているかを把握した上で使えるようになります。まずはローカルDBやよく使うAPIひとつだけを繋いでみるところから始めてみてください。

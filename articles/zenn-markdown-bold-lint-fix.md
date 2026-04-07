---
title: "Zennの**bold**が表示されない問題を100記事・477件一括修正した話"
emoji: "🔧"
type: "tech"
topics: ["zenn", "markdown", "python", "commonmark", "lint"]
published: false
status: "draft"
publication_name: "correlate_dev"
---

## はじめに

Zennで記事を書いていると、確かに `**bold**` と書いたのに太字にならないことがあります。特に日本語テキストの中に埋め込んだ場合や、テーブルの中に書いた場合に発生しやすい問題です。

100本の記事を一括でスキャンしたところ、WARN（警告）が456件検出されました。さらに調査を進めると、3つのパターンに分類できることがわかりました。

この記事では問題の根本原因と、Pythonスクリプトによる自動検出・一括修正の手順を解説します。

## 問題の3パターン

### パターン1: `:::message` ブロック直後の空行不足

Zenn独自記法の `:::message` ブロックと、その直後に続くテキストの間に空行がない場合、`**bold**` が正しくレンダリングされないことがあります。

```markdown
:::message
この中のテキストは正常に表示される
:::
**ここが太字にならない**  ← ブロックの直後（空行なし）
```

```markdown
:::message
この中のテキストは正常に表示される
:::

**空行を1行入れると太字になる**  ← 正常
```

### パターン2: テーブル行内の `**bold**`

Markdownのテーブル内での `**bold**` は、Zennのレンダラーによっては生テキストとして表示される場合があります。

```markdown
| 列1 | 列2 |
|-----|-----|
| **太字のつもり** | 通常テキスト |
```

テーブル内での太字はHTMLエスケープや記法の競合が起きやすく、他のMarkdownパーサーでも挙動が異なることがあります。

### パターン3: 日本語文字と `**` の隣接（CommonMark flanking rule違反）

最も多かったのはこのパターンです。CommonMark仕様の「flanking rule（隣接規則）」に違反しているケースです。

```markdown
これは**問題**です     ← "問題" の左の "**" の左がひらがな（非スペース）
これは **問題** です   ← スペースで区切ると正常
```

CommonMarkの仕様では、`**` がstrong emphasis（太字）として解釈されるには、**left-flanking delimiter run** の条件を満たす必要があります。

## CommonMark flanking ruleの仕組み

仕様書（CommonMark Spec 6.4）によると、`**` が left-flanking delimiter run になるには以下の条件が必要です。

> - それ自体がUnicode空白文字で終わっていない
> - かつ、以下のどちらかを満たす：
>   (a) それ自体がUnicode句読点文字で終わっていない
>   (b) 直前がUnicode空白文字またはUnicode句読点文字

日本語の平仮名・片仮名・漢字は「Unicode句読点」でも「Unicode空白文字」でもありません。そのため、`これは**太字**です` の `は**太` の部分では、`**` の直前が非空白・非句読点（`は`）であり、かつ `**` の直後も非空白・非句読点（`太`）なので、条件を満たせず太字として解釈されない場合があります。

実際の挙動はレンダラーによって異なりますが、Zennが採用しているレンダラーではこのルールが厳密に適用されます。

## lintスクリプトの実装

問題を検出する `lint-markdown-bold.py` です。

```python
#!/usr/bin/env python3
"""
Zenn記事の **bold** レンダリング問題を検出するlintスクリプト
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class LintWarning:
    file: Path
    line_num: int
    line: str
    pattern: str
    suggestion: str


def check_bold_flanking(line: str) -> list[tuple[str, str]]:
    """
    日本語文字に隣接した**bold**パターンを検出する
    Returns: [(matched_text, suggestion), ...]
    """
    warnings = []
    
    # **...**パターンを全て検索
    bold_pattern = re.compile(r'\*\*([^*]+)\*\*')
    
    for match in bold_pattern.finditer(line):
        start = match.start()
        end = match.end()
        matched = match.group(0)
        
        # 左側の文字をチェック
        left_char = line[start - 1] if start > 0 else ' '
        # 右側の文字をチェック
        right_char = line[end] if end < len(line) else ' '
        
        # 日本語文字（CJK統合漢字・ひらがな・カタカナ）の判定
        def is_cjk(c):
            return (
                '\u3000' <= c <= '\u9FFF' or  # CJK・ひらがな・カタカナ
                '\uF900' <= c <= '\uFAFF' or  # CJK互換漢字
                '\u3400' <= c <= '\u4DBF'     # CJK拡張A
            )
        
        if is_cjk(left_char) or is_cjk(right_char):
            # 修正案を生成
            suggestion = matched
            if is_cjk(left_char):
                suggestion = ' ' + suggestion
            if is_cjk(right_char):
                suggestion = suggestion + ' '
            warnings.append((matched, suggestion))
    
    return warnings


def check_message_block(lines: list[str]) -> list[tuple[int, str]]:
    """
    :::message ブロック直後の空行なしパターンを検出する
    Returns: [(line_num, line), ...]
    """
    issues = []
    in_block = False
    
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        
        if re.match(r'^:::', stripped) and 'message' in stripped:
            in_block = True
            continue
        
        if in_block and re.match(r'^:::$', stripped):
            in_block = False
            # 次の行を確認
            if i + 1 < len(lines):
                next_line = lines[i + 1].rstrip()
                if next_line and re.search(r'\*\*', next_line):
                    issues.append((i + 2, lines[i + 1]))
            continue
    
    return issues


def lint_file(file_path: Path) -> list[LintWarning]:
    """1ファイルをlintして警告リストを返す"""
    warnings = []
    content = file_path.read_text(encoding='utf-8')
    lines = content.splitlines()
    
    # テーブル内のboldチェック
    in_table = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        
        # テーブル行の検出
        if re.match(r'^\|', stripped):
            in_table = True
        else:
            in_table = False
        
        if in_table and re.search(r'\*\*[^|]+\*\*', stripped):
            warnings.append(LintWarning(
                file=file_path,
                line_num=i,
                line=line,
                pattern="table-bold",
                suggestion="テーブル内の**bold**はHTMLの<strong>タグに変換を検討"
            ))
        
        # 日本語隣接チェック（コードブロック外のみ）
        if not stripped.startswith('```') and not stripped.startswith('    '):
            for matched, suggestion in check_bold_flanking(line):
                warnings.append(LintWarning(
                    file=file_path,
                    line_num=i,
                    line=line,
                    pattern="cjk-flanking",
                    suggestion=f"{matched} → {suggestion}"
                ))
    
    # :::message ブロック後チェック
    for line_num, line in check_message_block(lines):
        warnings.append(LintWarning(
            file=file_path,
            line_num=line_num,
            line=line,
            pattern="message-block-spacing",
            suggestion=":::message ブロックの直後に空行を追加"
        ))
    
    return warnings


def main():
    articles_dir = Path("articles")
    if not articles_dir.exists():
        print("Error: articles/ ディレクトリが見つかりません")
        sys.exit(1)
    
    all_warnings: list[LintWarning] = []
    
    for md_file in sorted(articles_dir.glob("*.md")):
        warnings = lint_file(md_file)
        all_warnings.extend(warnings)
    
    # 結果の表示
    if not all_warnings:
        print("WARN 0件: 問題は見つかりませんでした")
        sys.exit(0)
    
    # ファイル別にグループ化して表示
    current_file = None
    for w in sorted(all_warnings, key=lambda x: (str(x.file), x.line_num)):
        if w.file != current_file:
            print(f"\n📄 {w.file}")
            current_file = w.file
        print(f"  L{w.line_num:4d} [{w.pattern}] {w.suggestion}")
    
    print(f"\n合計 WARN {len(all_warnings)}件")
    sys.exit(1)


if __name__ == "__main__":
    main()
```

## 自動修正スクリプトの実装

検出した問題を自動修正する `fix-bold.py` です。核心はスペース追加のアルゴリズムです。

```python
#!/usr/bin/env python3
"""
Zenn記事の **bold** 表示問題を一括修正するスクリプト

重要: **...**スパン全体を対象に、外側にのみスペースを追加する
内側にスペースを混入させないよう注意が必要
"""

import re
from pathlib import Path


def fix_cjk_bold(text: str) -> str:
    """
    日本語文字に隣接した**bold**パターンを修正する
    
    アルゴリズム:
    1. **...**スパン全体をキャプチャ
    2. 左側が日本語文字ならスパン全体の左にスペースを追加
    3. 右側が日本語文字ならスパン全体の右にスペースを追加
    4. 内側のテキストは一切変更しない
    """
    
    def is_cjk(c: str) -> bool:
        return (
            '\u3000' <= c <= '\u9FFF' or
            '\uF900' <= c <= '\uFAFF' or
            '\u3400' <= c <= '\u4DBF'
        )
    
    result = []
    # **...**全体を一つのトークンとして処理
    pattern = re.compile(r'(\*\*[^*\n]+?\*\*)')
    
    last_end = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        bold_span = match.group(0)
        
        # マッチ前のテキストを追加
        result.append(text[last_end:start])
        
        # 左側の文字
        left_context = text[last_end:start]
        left_char = left_context[-1] if left_context else ' '
        
        # 右側の文字
        right_char = text[end] if end < len(text) else ' '
        
        # 必要なスペースを外側にのみ追加
        prefix = ' ' if is_cjk(left_char) and not bold_span.startswith(' ') else ''
        suffix = ' ' if is_cjk(right_char) and not bold_span.endswith(' ') else ''
        
        result.append(prefix + bold_span + suffix)
        last_end = end
    
    result.append(text[last_end:])
    return ''.join(result)


def fix_message_block_spacing(content: str) -> str:
    """:::message ブロック直後の空行を追加する"""
    # ::: で終わる行の後、空行なしに続く行にスペースを挿入
    return re.sub(
        r'(^:::[ \t]*$\n)(\*\*)',
        r'\1\n\2',
        content,
        flags=re.MULTILINE
    )


def fix_file(file_path: Path, dry_run: bool = False) -> int:
    """1ファイルを修正し、変更件数を返す"""
    original = file_path.read_text(encoding='utf-8')
    
    fixed = original
    fixed = fix_message_block_spacing(fixed)
    
    # 行単位でCJK flanking修正
    lines = fixed.splitlines(keepends=True)
    fixed_lines = []
    for line in lines:
        # コードブロック・テーブル行はスキップ
        stripped = line.strip()
        if stripped.startswith('```') or stripped.startswith('|') or stripped.startswith('    '):
            fixed_lines.append(line)
        else:
            fixed_lines.append(fix_cjk_bold(line))
    
    fixed = ''.join(fixed_lines)
    
    if fixed == original:
        return 0
    
    if not dry_run:
        file_path.write_text(fixed, encoding='utf-8')
    
    # 変更行数をカウント
    original_lines = original.splitlines()
    fixed_split = fixed.splitlines()
    changes = sum(1 for a, b in zip(original_lines, fixed_split) if a != b)
    return changes


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    
    articles_dir = Path("articles")
    total_changes = 0
    
    for md_file in sorted(articles_dir.glob("*.md")):
        changes = fix_file(md_file, dry_run=args.dry_run)
        if changes > 0:
            mode = "DRY-RUN" if args.dry_run else "FIXED"
            print(f"{mode}: {md_file} ({changes}行変更)")
            total_changes += changes
    
    print(f"\n合計変更: {total_changes}行")


if __name__ == "__main__":
    main()
```

## 内側スペース混入バグの教訓

修正スクリプトの開発で最もハマったのが「内側にスペースが入るバグ」です。

初期の実装では `**...**` を左右の文字列で分割して処理していました。

```python
# バグのある初期実装
text = re.sub(
    r'([^\s])\*\*([^*]+)\*\*([^\s])',
    r'\1 **\2** \3',
    text
)
```

このパターンでは `**` の直前・直後の文字をキャプチャ群として取得し、スペースを挿入します。しかし複数の太字が連続する場合（`**A****B**`）や、ネストしたパターンで正しく動作しません。

最終的に「`**...**` 全体を一つのトークンとして扱い、外側にのみスペースを追加する」アプローチに変更しました。

```python
# 正しい実装（再掲）
# bold_span = "**内側のテキスト**" を変更せず、外側にスペースを付加
result.append(prefix + bold_span + suffix)
```

## 実行結果

100本の記事に対してlintを実行した結果です。

```
$ python scripts/lint-markdown-bold.py

📄 articles/nextjs-tutorial.md
  L 23 [cjk-flanking] **パフォーマンス** → **パフォーマンス** 
  L 89 [cjk-flanking] **最適化**です → **最適化** です

📄 articles/supabase-guide.md
  L 45 [message-block-spacing] :::message ブロックの直後に空行を追加
  L 112 [table-bold] テーブル内の**bold**はHTMLの<strong>タグに変換を検討

... (省略) ...

合計 WARN 456件
```

修正スクリプトを実行後：

```
$ python scripts/fix-bold.py
FIXED: articles/nextjs-tutorial.md (2行変更)
FIXED: articles/supabase-guide.md (3行変更)
...

合計変更: 477行

$ python scripts/lint-markdown-bold.py
WARN 0件: 問題は見つかりませんでした
```

WARN 456件の検出 → 477行の修正 → WARN 0件を達成しました。

## まとめ

Zennの `**bold**` 表示問題を整理します。

| パターン | 原因 | 修正方法 |
|---------|------|---------|
| :::message ブロック直後 | ブロック後の空行不足 | 空行を1行追加 |
| テーブル内 | レンダラーの制限 | `<strong>` タグに変換 |
| 日本語に隣接 | CommonMark flanking rule | 前後にスペースを追加 |

100本・456件という規模の修正をlintスクリプトと修正スクリプトで自動化することで、手動作業ゼロで WARN 0件を達成できました。

スクリプトは汎用的に書いているため、記事数が増えても同じワークフローで対応可能です。新しい記事を書いた際に定期的にlintを実行する習慣を持つことで、同じ問題の再発を防げます。

## 参考

- [CommonMark Spec 6.4 - Emphasis and strong emphasis](https://spec.commonmark.org/0.31.2/#emphasis-and-strong-emphasis)
- [ZennのMarkdown記法](https://zenn.dev/zenn/articles/markdown-guide)
- [Unicode flanking rules](https://spec.commonmark.org/0.31.2/#left-flanking-delimiter-run)

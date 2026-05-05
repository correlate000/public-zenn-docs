#!/usr/bin/env python3
"""単独 em-dash「—」(U+2014) を文脈に応じて置換する。

ルール:
- frontmatter `title:` 行 → 「：」
- 見出し（# で始まる行）→ 「：」
- リンクテキスト `[ ... — ... ](...)` → 「：」（リンク内のみ）
- 箇条書き / 通常本文 → 「：」（appositive用法が大半）
- テーブルのプレースホルダ `| — |` → そのまま（N/A表記）
- 引用元帰属 `> — [Author]` → そのまま（Western citation convention）
- コードフェンス内 → そのまま
"""
import os, re, time, shutil
from pathlib import Path

ART_DIR = Path(os.path.expanduser("~/dev/projects/self/public-zenn-docs/articles"))
BACKUP_DIR = Path(os.path.expanduser("~/dev/projects/self/public-zenn-docs/.single-emdash-backup"))


def fix_link_text_emdash(line: str):
    """リンクテキスト [...—...](...) のみを置換"""
    def repl(m):
        text = m.group(1)
        url = m.group(2)
        return '[' + text.replace('—', '：') + '](' + url + ')'
    return re.sub(r'\[([^\]]*?—[^\]]*?)\]\(([^)]+)\)', repl, line)


def process_file(path: Path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_fence = False
    in_fm = False
    out = []
    fixes = 0
    for i, raw in enumerate(lines):
        if i == 0 and raw.strip() == "---":
            in_fm = True
            out.append(raw)
            continue
        if in_fm and raw.strip() == "---":
            in_fm = False
            out.append(raw)
            continue
        s = raw.lstrip()
        if not in_fm and re.match(r'^(```+|~~~+)', s):
            in_fence = not in_fence
            out.append(raw)
            continue
        if in_fence:
            out.append(raw)
            continue
        if '—' not in raw or '——' in raw:
            out.append(raw)
            continue

        # 1. table placeholder: | — | を保護
        if re.search(r'\|\s*—\s*\|', raw):
            # その他の — があるか確認
            stripped = re.sub(r'\|\s*—\s*\|', '|||', raw)
            if '—' not in stripped:
                out.append(raw)
                continue
            # else: 後続処理へ（複数 — のうち table placeholder 以外を直す）

        # 2. blockquote attribution: > — [...] を保護
        if re.match(r'^\s*>\s*—\s', raw):
            out.append(raw)
            continue

        original = raw

        if in_fm and raw.lstrip().startswith('title:'):
            new = raw.replace('—', '：')
        elif raw.lstrip().startswith('#'):
            new = raw.replace('—', '：')
        else:
            # まずリンクテキスト内を処理
            new = fix_link_text_emdash(raw)
            # 残り（リンク外、テーブル外、引用外）の — を ：に
            # ただし | — | プレースホルダだけは残す
            # 簡易: テーブルプレースホルダを一時マーカーに置換 → 残り処理 → 戻す
            placeholder = '\x00TBLPH\x00'
            new = re.sub(r'(\|\s*)—(\s*\|)', lambda m: m.group(1) + placeholder + m.group(2), new)
            new = new.replace('—', '：')
            new = new.replace(placeholder, '—')
        if new != original:
            fixes += original.count('—') - new.count('—')
        out.append(new)
    return "\n".join(out), fixes


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    files = sorted(ART_DIR.glob("*.md"))
    summary = []
    total_files = 0
    total_fixes = 0
    for fp in files:
        new_text, n = process_file(fp)
        if n == 0:
            continue
        bak = BACKUP_DIR / f"{fp.stem}.{ts}.bak"
        shutil.copy2(fp, bak)
        fp.write_text(new_text, encoding="utf-8")
        summary.append((fp.name, n))
        total_files += 1
        total_fixes += n
    print(f"=== Single em-dash fix complete ===")
    print(f"Files modified: {total_files}")
    print(f"Total replacements: {total_fixes}")
    for name, n in sorted(summary, key=lambda x: -x[1])[:30]:
        print(f"  {n:3d}  {name}")
    if len(summary) > 30:
        print(f"  ... +{len(summary)-30} more")


if __name__ == "__main__":
    main()

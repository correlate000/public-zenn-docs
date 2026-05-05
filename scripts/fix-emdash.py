#!/usr/bin/env python3
"""emダッシュ「——」を文脈に応じて置換する。

ルール:
- frontmatter（先頭 `---` から次の `---` まで）の `title:` 行 → 「：」
- 見出し行（# で始まる行） → 「：」
- 本文 → 「。」
- コードフェンス内は対象外
"""
import os, re, time, shutil
from pathlib import Path

ART_DIR = Path(os.path.expanduser("~/dev/projects/self/public-zenn-docs/articles"))
BACKUP_DIR = Path(os.path.expanduser("~/dev/projects/self/public-zenn-docs/.emdash-fix-backup"))


def process_file(path: Path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_fence = False
    in_fm = False
    fm_started = False
    out = []
    fixes = 0
    for i, raw in enumerate(lines):
        # frontmatter detection
        if i == 0 and raw.strip() == "---":
            in_fm = True
            fm_started = True
            out.append(raw)
            continue
        if in_fm and raw.strip() == "---":
            in_fm = False
            out.append(raw)
            continue

        # code fence
        s = raw.lstrip()
        if not in_fm and re.match(r'^(```+|~~~+)', s):
            in_fence = not in_fence
            out.append(raw)
            continue
        if in_fence:
            out.append(raw)
            continue

        if "——" not in raw:
            out.append(raw)
            continue

        if in_fm:
            # frontmatter: only title/etc. — use ：
            if raw.lstrip().startswith("title:") or raw.lstrip().startswith("- "):
                new = raw.replace("——", "：")
            else:
                new = raw.replace("——", "：")
        elif raw.lstrip().startswith("#"):
            new = raw.replace("——", "：")
        else:
            new = raw.replace("——", "。")
        if new != raw:
            fixes += raw.count("——")
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
    print(f"=== Emdash fix complete ===")
    print(f"Files modified: {total_files}")
    print(f"Total replacements: {total_fixes}")
    for name, n in sorted(summary, key=lambda x: -x[1]):
        print(f"  {n:3d}  {name}")


if __name__ == "__main__":
    main()

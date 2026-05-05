#!/usr/bin/env python3
r"""崩れbold v2: インラインコードを含む bold も正しく扱う。

行内の各文字位置について「インラインコード内か」を先にマークし、
その上で `**...**` ペアを探して内側の前後余白を除去する。
コードフェンス内は対象外。
"""
import os, re, time, shutil
from pathlib import Path

ART_DIR = Path(os.path.expanduser("~/dev/projects/self/public-zenn-docs/articles"))
BACKUP_DIR = Path(os.path.expanduser("~/dev/projects/self/public-zenn-docs/.bold-fix-backup"))


def mark_protected(line: str):
    """インラインコード `...` の位置を True にしたマスクを返す。"""
    n = len(line)
    mask = [False] * n
    i = 0
    while i < n:
        if line[i] == '`':
            j = line.find('`', i + 1)
            if j == -1:
                break
            for k in range(i, j + 1):
                mask[k] = True
            i = j + 1
        else:
            i += 1
    return mask


def fix_line(line: str):
    mask = mark_protected(line)
    n = len(line)
    out = []
    i = 0
    fixes = 0
    while i < n:
        if i + 1 < n and line[i] == '*' and line[i + 1] == '*' and not mask[i]:
            # find closing ** (not inside inline code, no other * inside)
            j = i + 2
            found_close = -1
            while j + 1 < n:
                if line[j] == '*' and line[j + 1] == '*' and not mask[j]:
                    inner = line[i + 2:j]
                    # ensure no other unprotected '*' inside
                    bad = False
                    for k, c in enumerate(inner):
                        if c == '*' and not mask[i + 2 + k]:
                            bad = True
                            break
                    if not bad:
                        found_close = j
                        break
                j += 1
            if found_close != -1:
                inner = line[i + 2:found_close]
                stripped = inner.strip()
                if stripped and inner != stripped:
                    out.append('**' + stripped + '**')
                    fixes += 1
                else:
                    out.append(line[i:found_close + 2])
                i = found_close + 2
                continue
        out.append(line[i])
        i += 1
    return ''.join(out), fixes


def process_file(path: Path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_fence = False
    out_lines = []
    total = 0
    for raw in lines:
        s = raw.lstrip()
        if re.match(r'^(```+|~~~+)', s):
            in_fence = not in_fence
            out_lines.append(raw)
            continue
        if in_fence:
            out_lines.append(raw)
            continue
        new_line, n = fix_line(raw)
        total += n
        out_lines.append(new_line)
    return "\n".join(out_lines), total


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
        bak = BACKUP_DIR / f"{fp.stem}.{ts}.v2.bak"
        shutil.copy2(fp, bak)
        fp.write_text(new_text, encoding="utf-8")
        summary.append((fp.name, n))
        total_files += 1
        total_fixes += n
    print(f"=== Bold fix v2 complete ===")
    print(f"Files modified: {total_files}")
    print(f"Total replacements: {total_fixes}")
    for name, n in sorted(summary, key=lambda x: -x[1]):
        print(f"  {n:3d}  {name}")


if __name__ == "__main__":
    main()

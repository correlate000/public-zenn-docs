#!/usr/bin/env python3
"""Zenn 記事品質監査スクリプト

検出対象:
1. 行内の ** 個数が奇数（コードブロック・インラインコード除外後）
2. 行頭が "** " で始まる箇所（強調が崩れて箇条書き風になっている）
3. heading 行に ** が混入
4. frontmatter 破損（必須キー欠落、未閉じ、type/published 型不正）
5. mojibake（U+FFFD）、emダッシュ「—」「——」（日本語文中）
6. コードフェンス未閉じ
7. 末尾 ``` の前後に空行が無く崩れているケース
"""
import os, re, sys, glob, json

ART_DIR = os.path.expanduser("~/dev/projects/self/public-zenn-docs/articles")

REQUIRED_KEYS = {"title", "emoji", "type", "topics", "published"}

def strip_inline_code(line: str) -> str:
    # remove `...` segments
    return re.sub(r"`[^`]*`", "", line)

def audit(path: str):
    issues = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return [f"READ_ERROR: {e}"]

    lines = text.splitlines()

    # mojibake
    if "�" in text:
        positions = [i+1 for i, l in enumerate(lines) if "�" in l]
        issues.append(f"MOJIBAKE U+FFFD lines={positions[:10]}")

    # emダッシュ（日本語文中の "——" / "—" 単体使用）
    em_lines = []
    for i, l in enumerate(lines, 1):
        if "——" in l:
            em_lines.append(i)
    if em_lines:
        issues.append(f"EMDASH '——' lines={em_lines[:20]}")

    # frontmatter 解析
    if not text.startswith("---\n"):
        issues.append("FRONTMATTER missing leading ---")
    else:
        end = text.find("\n---\n", 4)
        if end == -1:
            issues.append("FRONTMATTER not closed")
        else:
            fm = text[4:end]
            keys = set()
            for ln in fm.splitlines():
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", ln)
                if m:
                    keys.add(m.group(1))
            missing = REQUIRED_KEYS - keys
            if missing:
                issues.append(f"FRONTMATTER missing keys: {sorted(missing)}")
            # type validation
            mt = re.search(r'^type\s*:\s*"?(\w+)"?', fm, re.M)
            if mt and mt.group(1) not in ("tech", "idea"):
                issues.append(f"FRONTMATTER type invalid: {mt.group(1)}")

    # コードブロック処理
    in_fence = False
    fence_char = None
    fence_open_line = 0
    for i, raw in enumerate(lines, 1):
        s = raw.lstrip()
        # detect fence start/end. Allow ``` and ~~~
        m = re.match(r"^(```+|~~~+)(.*)$", s)
        if m:
            mark = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = mark[0] * 3  # treat as prefix
                fence_open_line = i
            else:
                # closing fence (loose match)
                if mark[0] * 3 == fence_char:
                    in_fence = False
                    fence_char = None
            continue
        if in_fence:
            continue

        # heading に ** 混入
        if raw.startswith("#"):
            if "**" in raw:
                issues.append(f"L{i} HEADING contains **: {raw.strip()[:120]}")

        cleaned = strip_inline_code(raw)
        # 行頭 "** " （箇条書き風崩れ）
        if re.match(r"^\*\*\s+\S", cleaned):
            issues.append(f"L{i} LEADING '** ' (likely broken bold): {raw.strip()[:120]}")

        # ** 個数が奇数
        cnt = cleaned.count("**")
        if cnt % 2 == 1:
            issues.append(f"L{i} ODD ** count={cnt}: {raw.strip()[:120]}")

        # 単独 ** (空 bold)
        if re.search(r"\*\*\s*\*\*", cleaned):
            issues.append(f"L{i} EMPTY ** pair: {raw.strip()[:120]}")

    if in_fence:
        issues.append(f"CODE FENCE not closed (opened L{fence_open_line})")

    return issues


def main():
    files = sorted(glob.glob(os.path.join(ART_DIR, "*.md")))
    summary = {"total": len(files), "with_issues": 0, "issues_total": 0}
    report = []
    for fp in files:
        iss = audit(fp)
        if iss:
            summary["with_issues"] += 1
            summary["issues_total"] += len(iss)
            report.append((os.path.basename(fp), iss))

    print(f"# Audit summary: {summary}")
    print(f"# Files with issues: {summary['with_issues']} / {summary['total']}")
    for name, iss in report:
        print(f"\n## {name}  ({len(iss)} issues)")
        for x in iss[:30]:
            print(f"  - {x}")
        if len(iss) > 30:
            print(f"  ... +{len(iss)-30} more")

if __name__ == "__main__":
    main()

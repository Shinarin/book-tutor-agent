"""一次性迁移脚本：把 summaries/skeleton 旧文件名迁成纯 title 命名。

用法：
    python _migrate_run.py <book_dir> [--apply]

不带 --apply 是干跑（只打印计划+写计划文件），带 --apply 才真正执行。
"""

import json
import shutil
import sys
from pathlib import Path

from .filename_utils import sanitize_filename


def plan_dir(dir_path: Path, progress_source: Path):
    """根据 progress_source（summaries/progress.json）算出 dir_path 下的 rename 计划。

    返回 [(seq, title, old_name, new_name, old_exists, new_exists, same_file)]
    """
    progress = json.loads(progress_source.read_text(encoding="utf-8"))
    items = sorted(progress.items(), key=lambda x: int(x[0]))
    rows = []
    for seq, entry in items:
        title = entry["title"]
        old_name = entry["file_name"]
        new_name = sanitize_filename(title) + ".md"
        old_path = dir_path / old_name
        new_path = dir_path / new_name
        rows.append({
            "seq": int(seq),
            "title": title,
            "old": old_name,
            "new": new_name,
            "old_exists": old_path.exists(),
            "new_exists": new_path.exists(),
            "same_file": old_path.exists() and new_path.exists() and old_path.resolve() == new_path.resolve(),
        })
    return rows, progress, items


def build_index(progress: dict, kind: str, book_total_lines: int) -> str:
    items = sorted(progress.items(), key=lambda x: int(x[0]))
    lines = [f"# 全书 {kind} 章节次序", "", "按原书顺序列出。决定章节先后、相邻关系时以此为准。", ""]
    prev_next_offset = 0
    for i, (seq, entry) in enumerate(items):
        title = entry["title"]
        new_name = sanitize_filename(title) + ".md"
        if i == 0:
            start_line = 1
        else:
            start_line = prev_next_offset + 1
        if entry["next_offset"] == "END":
            end_line = book_total_lines
        else:
            end_line = int(entry["next_offset"])
        lines.append(f"{i + 1}. [{title}]({new_name})  [L{start_line}-L{end_line}]")
        if entry["next_offset"] != "END":
            prev_next_offset = int(entry["next_offset"])
    return "\n".join(lines) + "\n"


def find_book_md(book_dir: Path) -> Path:
    candidates = [p for p in book_dir.glob("*.md") if p.name not in {"index.md", "all_summaries.md"}]
    candidates = [p for p in candidates if p.parent == book_dir]
    if not candidates:
        raise SystemExit(f"找不到 {book_dir} 下的书正文 .md 文件")
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法: python _migrate_run.py <book_dir> [--apply]")
    book_dir = Path(sys.argv[1])
    apply = "--apply" in sys.argv[2:]

    summaries = book_dir / "summaries"
    skeleton = book_dir / "skeleton"
    progress_path = summaries / "progress.json"
    if not progress_path.exists():
        raise SystemExit(f"找不到 {progress_path}")

    book_md = find_book_md(book_dir)
    book_total_lines = sum(1 for _ in book_md.open(encoding="utf-8"))

    report_lines = []
    report_lines.append(f"book_dir: {book_dir}")
    report_lines.append(f"book_md : {book_md.name}  (total lines = {book_total_lines})")
    report_lines.append("")

    plans = {}
    for sub in (summaries, skeleton):
        if not sub.exists():
            report_lines.append(f"[skip] {sub.name}/ 不存在")
            continue
        rows, progress, items = plan_dir(sub, progress_path)
        plans[sub.name] = (sub, rows, progress)
        report_lines.append(f"=== {sub.name}/ ===")
        for r in rows:
            tag = []
            if r["old"] == r["new"]:
                tag.append("SKIP-same-name")
            if not r["old_exists"] and r["old"] != r["new"]:
                tag.append("MISSING-old")
            if r["new_exists"] and not r["same_file"]:
                tag.append("CONFLICT-new-exists")
            tagstr = f"  [{', '.join(tag)}]" if tag else ""
            report_lines.append(f"  {r['seq']:>2}. {r['old']}  ->  {r['new']}{tagstr}")
        report_lines.append("")

    report = "\n".join(report_lines)
    plan_file = book_dir / "_migration_plan.txt"
    plan_file.write_text(report, encoding="utf-8")
    print(f"[plan written to] {plan_file}")
    print(report)

    if not apply:
        print("\n[dry-run] 加 --apply 真正执行")
        return

    print("\n[apply] 开始执行...")
    conflicts = []
    for name, (sub, rows, _) in plans.items():
        for r in rows:
            if r["new_exists"] and not r["same_file"] and r["old"] != r["new"]:
                conflicts.append(f"{sub}/{r['new']}")
    if conflicts:
        raise SystemExit(f"冲突，请人工处理:\n" + "\n".join(conflicts))

    bak = progress_path.with_suffix(".json.bak")
    shutil.copy2(progress_path, bak)
    print(f"  [backup] {progress_path} -> {bak}")

    for name, (sub, rows, progress) in plans.items():
        for r in rows:
            if r["old"] == r["new"]:
                continue
            if not r["old_exists"]:
                print(f"  [warn] {sub}/{r['old']} 不存在，跳过")
                continue
            (sub / r["old"]).rename(sub / r["new"])
            print(f"  [rename] {sub.name}/{r['old']}  ->  {r['new']}")

    for seq, entry in plans["summaries"][2].items():
        entry["file_name"] = sanitize_filename(entry["title"]) + ".md"
    progress_path.write_text(
        json.dumps(plans["summaries"][2], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  [write] {progress_path}")

    for name, (sub, rows, progress) in plans.items():
        index_md = build_index(progress, name, book_total_lines)
        (sub / "index.md").write_text(index_md, encoding="utf-8")
        print(f"  [write] {sub / 'index.md'}")

    print("[done]")


if __name__ == "__main__":
    main()

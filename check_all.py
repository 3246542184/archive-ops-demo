#!/usr/bin/env python3
"""一致性检查的演示版:备份是否忠实、文档结构是否完整、回滚资源是否齐备、工作区是否干净。

只演示检查方法,不含任何真实路径与数据。用法:

    python check_all.py --source samples/source --backup samples/backup \
                        --rollback samples/rollback

四项检查各自的对象是分开的:
    备份检查 -> --source 与 --backup
    文档检查 -> --docs(默认取 --source)
    回滚检查 -> --rollback
    Git 检查 -> --git-root(默认是脚本所在目录,即项目根)

退出码:0 没有失败项;1 有失败项。
"未检测"(例如 git 不可用、目录不是仓库)不算失败,但也不会写成"全部通过"。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(root: str, errors: list[str] | None = None) -> dict[str, str]:
    """返回 {相对路径: 绝对路径}。

    errors 不为 None 时,遍历错误(权限不足、路径消失等)会记进去。
    os.walk 默认静默跳过读不了的目录 —— 对一个"证明备份忠实"的工具来说,
    "没看见"不能等于"没问题",所以这里把错误显式交回调用方。
    """
    out = {}
    if errors is None:
        errors = []

    def _on_error(exc: OSError) -> None:
        errors.append("%s: %s" % (getattr(exc, "filename", "?"), exc.strerror or exc))

    for dirpath, dirs, files in os.walk(root, onerror=_on_error):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in files:
            full = os.path.join(dirpath, name)
            out[os.path.relpath(full, root)] = full
    return out


def check_backup(source: str, backup: str) -> tuple[bool, str]:
    """逐个文件比大小与 SHA-256。缺文件、不一致算失败;备份里多出的文件只统计,不算失败。"""
    if not os.path.isdir(source) or not os.path.isdir(backup):
        return None, "[备份] 未检测:源目录或备份目录不存在"
    walk_errors: list[str] = []
    src = walk_files(source, walk_errors)
    bak = walk_files(backup, walk_errors)
    if walk_errors:
        text = "[备份] 未检测:有 %d 处目录无法读取,本次结论不可信" % len(walk_errors)
        for e in walk_errors[:10]:
            text += "\n        无法读取: %s" % e
        return None, text
    same = 0
    missing, differing = [], []
    for rel, full in sorted(src.items()):
        peer = bak.get(rel)
        if peer is None:
            missing.append(rel)
            continue
        if os.path.getsize(full) != os.path.getsize(peer) or sha256_of(full) != sha256_of(peer):
            differing.append(rel)
            continue
        same += 1
    extra = sorted(set(bak) - set(src))
    text = "[备份] 一致 %d 个 · 缺失 %d 个 · 不一致 %d 个 · 备份多出 %d 个" % (
        same, len(missing), len(differing), len(extra))
    for rel in missing:
        text += "\n        缺失: %s" % rel
    for rel in differing:
        text += "\n        不一致: %s" % rel
    for rel in extra:
        text += "\n        仅存在于备份: %s(不算失败,仅提示)" % rel
    return (not missing and not differing), text


def check_docs(root: str) -> tuple[bool, str]:
    """结构检查,范围有限:末尾换行、前五行有标题、代码围栏成对。不检查内容正确性。"""
    if not os.path.isdir(root):
        return None, "[文档] 未检测:目录不存在"
    total = failed = 0
    problems = []
    walk_errors: list[str] = []
    for rel, full in sorted(walk_files(root, walk_errors).items()):
        if not rel.lower().endswith(".md"):
            continue
        total += 1
        with open(full, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", "replace")
        reasons = []
        if not raw.endswith(b"\n"):
            reasons.append("末尾缺换行")
        if not any(line.lstrip().startswith("#") for line in text.splitlines()[:5]):
            reasons.append("前五行没有标题")
        if text.count("```") % 2 != 0:
            reasons.append("代码围栏不成对")
        if reasons:
            failed += 1
            problems.append("%s: %s" % (rel, "、".join(reasons)))
    if walk_errors:
        text = "[文档] 未检测:有 %d 处目录无法读取,本次结论不可信" % len(walk_errors)
        for e in walk_errors[:10]:
            text += "\n        无法读取: %s" % e
        return None, text
    if total == 0:
        return None, "[文档] 未检测:目录里没有 Markdown 文件"
    out = "[文档] 结构检查 %d 篇 · 通过 %d · 失败 %d(只查格式,不查内容)" % (
        total, total - failed, failed)
    for p in problems:
        out += "\n        问题: %s" % p
    return failed == 0, out


def resolve_inside(root: str, name: str) -> str | None:
    """把清单里的文件名解析成回滚目录内的路径;越界(绝对路径或 ..)返回 None。"""
    if not name or os.path.isabs(name) or name.startswith(("~", "/", "\\")):
        return None
    root_abs = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_abs, name))
    if candidate != root_abs and not candidate.startswith(root_abs + os.sep):
        return None
    return candidate


def check_rollback(root: str) -> tuple[bool, str]:
    """manifest.json 登记的安装包:存在、路径在目录内、且哈希匹配才算合格。"""
    manifest = os.path.join(root, "manifest.json")
    if not os.path.isfile(manifest):
        return None, "[回滚] 未检测:这个目录里没有 manifest.json"
    try:
        with open(manifest, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return False, "[回滚] 清单无法解析: %s: %s" % (type(exc).__name__, exc)
    if not isinstance(data, dict):
        return False, "[回滚] 清单顶层必须是对象,实际是 %s" % type(data).__name__
    entries = data.get("installers") or []
    if not isinstance(entries, list):
        return False, "[回滚] installers 必须是数组,实际是 %s" % type(entries).__name__
    bad_items = [i for i, item in enumerate(entries) if not isinstance(item, dict)]
    if bad_items:
        return False, "[回滚] installers 第 %s 项不是对象,清单结构不合法" % (
            "、".join(str(i + 1) for i in bad_items[:5]))
    ok = missing = nohash = bad = unsafe = 0
    detail = []
    for item in entries:
        name = str(item.get("file") or "")
        want = str(item.get("sha256") or "").strip().lower()
        path = resolve_inside(root, name)
        if path is None:
            unsafe += 1
            detail.append("%s: 路径越界或非法,已拒绝" % (name or "(空)"))
            continue
        if not os.path.isfile(path):
            missing += 1
            detail.append("%s: 文件缺失" % name)
            continue
        if not want:
            nohash += 1
            detail.append("%s: 清单里没有登记 SHA-256" % name)
            continue
        if sha256_of(path) != want:
            bad += 1
            detail.append("%s: 哈希不符" % name)
            continue
        ok += 1
    out = "[回滚] 安装包 %d 个 · 合格 %d · 缺失 %d · 未填哈希 %d · 哈希不符 %d · 路径越界 %d" % (
        len(entries), ok, missing, nohash, bad, unsafe)
    for d in detail:
        out += "\n        %s" % d
    return (missing == 0 and nohash == 0 and bad == 0 and unsafe == 0), out


def check_git(root: str) -> tuple[bool | None, str]:
    """读取工作区状态。目录不是 Git 仓库、或本机没有 git 时,返回 None 表示未检测。"""
    git_marker = os.path.join(root, ".git")
    if not os.path.exists(git_marker):
        return None, "[Git ] 未检测:该目录不是 Git 仓库"
    try:
        proc = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except FileNotFoundError:
        return None, "[Git ] 未检测:本机没有 git 命令"
    except subprocess.TimeoutExpired:
        return None, "[Git ] 未检测:git 命令超时"
    if proc.returncode != 0:
        return None, "[Git ] 未检测:git 返回 %d" % proc.returncode
    changed = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if changed:
        out = "[Git ] 未提交改动 %d 项" % len(changed)
        for line in changed[:10]:
            out += "\n        %s" % line[:80]
        return False, out
    return True, "[Git ] 未提交改动 0 项"


def summarize(failures: int, skipped: int) -> str:
    """三种汇总语义分开,不把"有未检测"说成"全部通过"。"""
    if failures:
        tail = " · %d 项未检测" % skipped if skipped else ""
        return "结果: %d 项未通过%s" % (failures, tail)
    if skipped:
        return "结果: 无失败项 · %d 项未检测" % skipped
    return "结果: 全部通过"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="备份与变更一致性检查(演示版)")
    ap.add_argument("--source", default="samples/source", help="备份源目录")
    ap.add_argument("--backup", default="samples/backup", help="备份目录")
    ap.add_argument("--rollback", default="samples/rollback", help="回滚资源目录")
    ap.add_argument("--docs", default=None, help="文档目录,默认取 --source")
    ap.add_argument("--git-root", default=HERE, help="Git 检查的仓库根目录,默认脚本所在目录")
    args = ap.parse_args(argv)

    docs_root = args.docs or args.source
    results = [
        check_backup(args.source, args.backup),
        check_docs(docs_root),
        check_rollback(args.rollback),
        check_git(args.git_root),
    ]

    failures = skipped = 0
    for ok, text in results:
        print(text)
        if ok is None:
            skipped += 1
        elif not ok:
            failures += 1

    print()
    print(summarize(failures, skipped))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

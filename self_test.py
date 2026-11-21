#!/usr/bin/env python3
"""反例自检:确认检查不会"永远通过"。

每一组都有明确断言:结果不符合预期就记一次失败,最后按失败数返回退出码(0 通过 / 1 失败)。

十一组测试场景:
  0  示例数据本身(故意含缺陷)        应判失败
  1  回滚清单缺 SHA-256              应判失败,报"未填哈希"
  2  备份里多出文件                   应提示,且不当作缺失
  3  Markdown 末尾缺换行              应判失败,报"末尾缺换行"
  3b 空文档目录                       应判未检测,不报通过
  4  回滚清单路径越界(../)          应判失败,报"路径越界"
  4b 链接指向回滚目录外               应判失败;符号链接不可用时退用目录联接
  5/6 Git 干净仓库与有未提交改动      干净判通过,脏判失败并列出文件名
  6b 真实 git worktree(干净与脏)     干净判通过,脏判失败
  6c .git 是文件(伪造)               不应被误判为"不是仓库"
  7  汇总语义三种状态                 全部通过 / 无失败项·N 未检测 / N 项未通过

用法: python self_test.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import check_all  # noqa: E402

FAILS = []  # type: list[str]
SKIPS = []  # type: list[str]


def expect(name, cond, hint=""):
    if cond:
        print("  [通过] %s" % name)
    else:
        print("  [失败] %s%s" % (name, ("  <- " + hint) if hint else ""))
        FAILS.append(name)


def skip(name, why):
    print("  [跳过] %s(%s)" % (name, why))
    SKIPS.append(name)


def git_available():
    try:
        return subprocess.run(["git", "--version"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def make_manifest(role, entries):
    data = {"note": "自检用清单", "generated": "2026-11-21", "installers": entries, "rules": []}
    io.open(os.path.join(role, "manifest.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    samples = os.path.join(HERE, "samples")
    tmp = tempfile.mkdtemp(prefix="demo-self-test-")
    try:
        print("== 0. 示例数据本身 ==")
        ok, text = check_all.check_backup(os.path.join(samples, "source"), os.path.join(samples, "backup"))
        expect("示例备份应判失败(故意缺一个、改一个)", ok is False, text.splitlines()[0])
        ok, text = check_all.check_rollback(os.path.join(samples, "rollback"))
        expect("示例回滚清单应判失败(登记了不存在的包)", ok is False, text.splitlines()[0])

        print("== 1. 回滚清单缺 SHA-256 ==")
        role = os.path.join(tmp, "rollback-nohash")
        shutil.copytree(os.path.join(samples, "rollback"), role)
        make_manifest(role, [{"version": "1.0", "file": "tool-1.0.bin"}])
        ok, text = check_all.check_rollback(role)
        expect("缺哈希应判失败", ok is False, text.splitlines()[0])
        expect("消息里应出现\"未填哈希\"", "未填哈希" in text, text)
        expect("合格数应为 0", "合格 0" in text, text)

        print("== 2. 备份里多出文件 ==")
        bak = os.path.join(tmp, "backup-extra")
        shutil.copytree(os.path.join(samples, "backup"), bak)
        with open(os.path.join(bak, "surplus.md"), "w", encoding="utf-8") as f:
            f.write("# 只在备份里\n")
        ok, text = check_all.check_backup(os.path.join(samples, "source"), bak)
        expect("消息里应列出仅存在于备份的文件", "仅存在于备份" in text, text)
        expect("多出文件不应被当作缺失", "缺失: surplus.md" not in text, text)

        print("== 3. Markdown 末尾缺换行 ==")
        docs = os.path.join(tmp, "docs")
        os.makedirs(docs, exist_ok=True)
        p = os.path.join(docs, "bad.md")
        io.open(p, "w", encoding="utf-8", newline="\n").write("# 标题\n\n正文")
        io.open(p, "wb").write(io.open(p, "rb").read().rstrip(b"\n"))
        ok, text = check_all.check_docs(docs)
        expect("缺末尾换行应判失败", ok is False, text.splitlines()[0])
        expect("消息里应出现\"末尾缺换行\"", "末尾缺换行" in text, text)

        print("== 3b. 空文档目录应判未检测,而不是通过 ==")
        empty = os.path.join(tmp, "docs-empty")
        os.makedirs(empty, exist_ok=True)
        ok, text = check_all.check_docs(empty)
        expect("空目录应返回未检测(None)", ok is None, text)
        expect("消息里应说明没有 Markdown 文件", "没有 Markdown 文件" in text, text)

        print("== 4. 回滚清单路径越界 ==")
        role2 = os.path.join(tmp, "rollback-escape")
        os.makedirs(role2, exist_ok=True)
        outside = os.path.join(tmp, "outside.txt")
        io.open(outside, "w", encoding="utf-8").write("本文件在回滚目录之外\n")
        make_manifest(role2, [{"version": "9", "file": "../outside.txt", "sha256": "0" * 64}])
        ok, text = check_all.check_rollback(role2)
        expect("越界路径应判失败", ok is False, text.splitlines()[0])
        expect("消息里应出现\"路径越界\"", "路径越界" in text, text)
        expect("越界文件不应被读取计数为合格", "合格 0" in text, text)

        print("== 4b. 链接指向回滚目录外 ==")
        role3 = os.path.join(tmp, "rollback-link")
        os.makedirs(role3, exist_ok=True)
        outdir = os.path.join(tmp, "outside-dir")
        os.makedirs(outdir, exist_ok=True)
        secret = os.path.join(outdir, "secret.bin")
        io.open(secret, "w", encoding="utf-8").write("目录外的文件\n")

        link_path = os.path.join(role3, "link")
        entry_name = None
        try:  # 先试符号链接(Linux/macOS 或已开开发者模式的 Windows)
            os.symlink(outdir, link_path)
            entry_name = "link/secret.bin"
        except (OSError, NotImplementedError):
            pass
        if entry_name is None and os.name == "nt":
            # Windows 普通账户建不了符号链接,退用目录联接(junction),同样能穿出目录
            r = subprocess.run(["cmd", "/c", "mklink", "/J", link_path, outdir],
                               capture_output=True, text=True)
            if r.returncode == 0:
                entry_name = "link/secret.bin"
        if entry_name is None:
            skip("链接越界", "本机既不允许符号链接,也建不了目录联接")
        else:
            make_manifest(role3, [{"version": "9", "file": entry_name,
                                   "sha256": check_all.sha256_of(secret)}])
            ok, text = check_all.check_rollback(role3)
            expect("链接越界应判失败", ok is False, text.splitlines()[0])
            expect("应报\"路径越界\"", "路径越界" in text, text)
            expect("不应把越界文件算作合格", "合格 0" in text, text)

        print("== 5/6. Git 检查 ==")
        if not git_available():
            skip("Git 反例(干净/脏)", "本机没有 git")
        else:
            repo = os.path.join(tmp, "gitrepo")
            os.makedirs(repo, exist_ok=True)
            env = dict(os.environ, GIT_AUTHOR_NAME="demo", GIT_AUTHOR_EMAIL="demo@example.invalid",
                       GIT_COMMITTER_NAME="demo", GIT_COMMITTER_EMAIL="demo@example.invalid")
            for cmd in (["git", "init", "-q"], ["git", "add", "."],
                        ["git", "commit", "-qm", "init", "--allow-empty"]):
                subprocess.run(cmd, cwd=repo, capture_output=True, env=env, timeout=30)
            ok, text = check_all.check_git(repo)
            expect("干净仓库应判通过", ok is True, text)
            with open(os.path.join(repo, "dirty.txt"), "w", encoding="utf-8") as f:
                f.write("未提交\n")
            ok, text = check_all.check_git(repo)
            expect("有未提交改动应判失败", ok is False, text.splitlines()[0])
            expect("消息里应出现具体文件名", "dirty.txt" in text, text)

            print("== 6b. 真实 git worktree(干净与脏) ==")
            base_repo = os.path.join(tmp, "wt-base")
            os.makedirs(base_repo, exist_ok=True)
            for cmd in (["git", "init", "-q"], ["git", "add", "."],
                        ["git", "commit", "-qm", "init", "--allow-empty"]):
                subprocess.run(cmd, cwd=base_repo, capture_output=True, env=env, timeout=30)
            wt_path = os.path.join(tmp, "wt-copy")
            r = subprocess.run(["git", "worktree", "add", "-q", wt_path],
                               cwd=base_repo, capture_output=True, text=True, env=env, timeout=60)
            if r.returncode != 0:
                skip("真实 worktree 用例", (r.stderr or "").strip()[:40] or "git worktree 失败")
            else:
                expect("worktree 里 .git 是文件", os.path.isfile(os.path.join(wt_path, ".git")),
                       os.path.join(wt_path, ".git"))
                ok, text = check_all.check_git(wt_path)
                expect("worktree 干净时应判通过(而不是未检测)", ok is True, text)
                with open(os.path.join(wt_path, "wt-dirty.txt"), "w", encoding="utf-8") as f:
                    f.write("未提交\n")
                ok, text = check_all.check_git(wt_path)
                expect("worktree 有改动时应判失败", ok is False, text.splitlines()[0])
                expect("消息里应出现该文件名", "wt-dirty.txt" in text, text)

            print("== 6c. .git 是文件(伪造的 gitdir)不应被误判为\"不是仓库\" ==")
            wt = os.path.join(tmp, "worktree-like")
            os.makedirs(wt, exist_ok=True)
            io.open(os.path.join(wt, ".git"), "w", encoding="utf-8").write(
                "gitdir: /nonexistent/gitdir\n")
            ok, text = check_all.check_git(wt)
            expect("含 .git 文件时不应报\"不是 Git 仓库\"", "不是 Git 仓库" not in text, text)
            expect("应报未检测(None)", ok is None, text)

        print("== 7. 汇总输出 ==")
        s0 = check_all.summarize(0, 0)
        s1 = check_all.summarize(0, 1)
        s2 = check_all.summarize(2, 1)
        expect("无失败无跳过 -> 全部通过", s0 == "结果: 全部通过", s0)
        expect("无失败有跳过 -> 不应写\"全部通过\"", ("未检测" in s1 and "全部通过" not in s1), s1)
        expect("有失败 -> 应写未通过数", ("2 项未通过" in s2 and "1 项未检测" in s2), s2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if SKIPS:
        print("跳过 %d 项:%s" % (len(SKIPS), "、".join(SKIPS)))
    if FAILS:
        print("结果: %d 项不符合预期" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("结果: 全部符合预期" + ("(含 %d 项跳过)" % len(SKIPS) if SKIPS else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""造一份示例数据:源目录 3 个文件,备份目录故意少一个、改一个,用来演示检查能抓到问题。

生成后目录结构:

    samples/source/notes.md
    samples/source/plan.txt
    samples/source/table.md
    samples/backup/notes.md      (与源一致)
    samples/backup/plan.txt      (内容被改过 -> 不一致)
                                  (table.md 缺失)
    samples/rollback/manifest.json
    samples/rollback/tool-1.0.bin  (哈希正确)
    samples/rollback/tool-2.0.bin  (缺失)

只写 samples/ 目录,不碰别处。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")

SOURCE = {
    "notes.md": "# 会议记录\n\n- 备份改到每周一次\n- 回滚包保留两个版本\n",
    "plan.txt": "第一步 快照\n第二步 改动\n第三步 对比\n",
    "table.md": "# 检查清单\n\n| 项 | 结果 |\n|---|---|\n| 备份 | 通过 |\n",
}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    src = os.path.join(SAMPLES, "source")
    bak = os.path.join(SAMPLES, "backup")
    role = os.path.join(SAMPLES, "rollback")

    # 先清掉本脚本自己管理的三个子目录,保证重复运行得到同样的结果
    # (只删 samples/ 下的 source / backup / rollback,不碰其它任何路径)
    for d in (src, bak, role):
        if os.path.isdir(d):
            shutil.rmtree(d)

    for name, text in SOURCE.items():
        write(os.path.join(src, name), text)

    # 备份:notes.md 完全一致;plan.txt 被改过;table.md 不复制(制造"缺失")
    write(os.path.join(bak, "notes.md"), SOURCE["notes.md"])
    write(os.path.join(bak, "plan.txt"), "第一步 快照\n第二步 改动(这一行在备份里被改过)\n")
    write(os.path.join(bak, "extra.md"), "# 只在备份里存在的文件\n")

    # 回滚资源:一个真实文件 + 一个登记了但不存在的文件
    os.makedirs(role, exist_ok=True)
    tool = os.path.join(role, "tool-1.0.bin")
    with open(tool, "wb") as f:
        f.write(b"DEMO-INSTALLER-1.0\n")
    manifest = {
        "note": "演示用的回滚清单,文件名与哈希都是示例",
        "generated": "2026-11-21",
        "installers": [
            {"version": "1.0", "file": "tool-1.0.bin", "sha256": sha256_of(tool)},
            {"version": "2.0", "file": "tool-2.0.bin", "sha256": "0" * 64},
        ],
        "rules": ["安装前一概先做快照", "每个版本都保留安装包与哈希"],
    }
    write(os.path.join(role, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("已生成示例数据:", SAMPLES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

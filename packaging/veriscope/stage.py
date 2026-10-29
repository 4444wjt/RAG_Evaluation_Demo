"""把仓库里的法条诊断源码按原相对路径复制进包内 _root/，并复制 LICENSE。

构建前运行一次；_root/ 与 LICENSE 是生成物，已在本目录 .gitignore 中忽略。
复制按字节进行，不改任何内容；安装后报告里的 codeSha256 应与仓库文件一致。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEST = HERE / "src" / "veriscope" / "_root"
FILES = (
    "scripts/statute_diagnostic.py",
    "backend/__init__.py",
    "backend/core/__init__.py",
    "backend/core/statute_check.py",
    "backend/core/statute_locate.py",
    "examples/statute_check/constructed-request.json",
)


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    for name in FILES:
        src, dst = REPO / name, DEST / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        print(hashlib.sha256(dst.read_bytes()).hexdigest(), name)
    (HERE / "LICENSE").write_bytes((REPO / "LICENSE").read_bytes())
    print("LICENSE copied")


if __name__ == "__main__":
    main()

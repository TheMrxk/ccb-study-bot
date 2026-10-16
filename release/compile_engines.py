"""镜像 builder 阶段执行：把引擎 .py 编译为 .so，保持包目录结构。

在 Docker 多阶段构建的 builder 容器内运行（与 runtime 同源 python:3.12-slim，
保证 .so 的 Python 版本/架构/glibc 与运行镜像一致）。

做法（最标准的 setuptools 方式，避免手工 build_ext 路径问题）：
  1. 把 app 复制成工作树；
  2. 对 engine.list 中每个引擎 AST 剥 docstring（原地覆盖工作树里的副本）；
  3. 在工作树根生成 setup.py，扩展名带包前缀（services.xxx），build_ext --inplace；
  4. 产物 services/*.so 收集到 out 根。

入参：
    --src    app 源码根（会被复制到内部工作树，不改原件）
    --list   engine.list
    --out    .so 输出根
    --strip  strip_docstrings.py 所在目录
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def read_engine_list(list_path: Path) -> list[str]:
    items = []
    for line in list_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(line)
    return items


SETUP_TMPL = '''\
from setuptools import setup, Extension
from Cython.Build import cythonize

PAIRS = {pairs!r}

extensions = [
    Extension(name=name, sources=[src])
    for name, src in PAIRS
]

setup(
    script_args=["build_ext", "--inplace"],
    ext_modules=cythonize(
        extensions,
        compiler_directives={{"language_level": "3", "annotation_typing": False, "embedsignature": True}},
    ),
)
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strip", required=True)
    args = ap.parse_args()

    sys.path.insert(0, args.strip)
    from strip_docstrings import strip_file  # noqa: E402

    src_root = Path(args.src)
    out_root = Path(args.out)
    work = Path("/build/_work")

    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src_root, work)
    out_root.mkdir(parents=True, exist_ok=True)

    engines = read_engine_list(Path(args.list))
    print(f"[compile] 待编译引擎 {len(engines)} 个")

    # 1) 原地剥 docstring
    for rel in engines:
        p = work / rel
        if not p.exists():
            print(f"[compile][ERROR] 引擎源码缺失: {rel}", file=sys.stderr)
            return 1
        strip_file(str(p), str(p))

    # 2) 生成 setup.py（模块名带包前缀）
    pairs = []
    for rel in engines:
        rel_path = Path(rel)
        module = ".".join(rel_path.with_suffix("").parts)  # services.ccb_api
        pairs.append((module, rel))
    (work / "setup_compile.py").write_text(
        SETUP_TMPL.format(pairs=pairs), encoding="utf-8"
    )

    # 3) 编译（输出直接继承终端，gcc 报错完整可见）
    proc = subprocess.run(
        [sys.executable, "setup_compile.py"],
        cwd=work,
    )
    if proc.returncode != 0:
        print("[compile][ERROR] Cython/gcc 编译失败", file=sys.stderr)
        return proc.returncode

    # 4) 收集 .so
    produced = []
    for rel in engines:
        stem = Path(rel).stem
        sos = list((work / Path(rel).parent).glob(stem + "*.so"))
        if not sos:
            print(f"[compile][ERROR] 编译后未找到 .so: {rel}", file=sys.stderr)
            return 1
        for so in sos:
            dst = out_root / Path(rel).parent / so.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(so, dst)
            produced.append(str(Path(rel).parent / so.name))

    print("[compile] 完成，产物：")
    for p in sorted(produced):
        print("   ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())

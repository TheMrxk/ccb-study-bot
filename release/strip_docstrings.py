"""AST 剥离器：删除模块 / 函数 / 类的 docstring，再交给 Cython。

只作用于构建期的临时副本，绝不触碰私有源码。
实测：不剥离时 strings 可从 .so 挖出模块开头的算法说明注释（默认保留 docstring）；
      剥离后特征串命中归零（仅剩 Cython 自身符号）。
注意：Cython 的 doc_string 指令无效（unknown directive），必须在编译前用 AST 处理。

用法（库形式）：
    from strip_docstrings import strip_file
    strip_file("src/sm3.py", "out/sm3.py")
也可命令行：python strip_docstrings.py <src> <dst>
"""
from __future__ import annotations

import ast
import sys


def _is_docstring_expr(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(getattr(stmt, "value", None), ast.Constant)  # type: ignore[arg-type]
 and isinstance(stmt.value.value, str)  # type: ignore[union-attr]
    )


def strip_source(src: str) -> str:
    tree = ast.parse(src)

    # 删模块 docstring
    if tree.body and _is_docstring_expr(tree.body[0]):
        tree.body.pop(0)

    # 删函数/方法/类 docstring
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and _is_docstring_expr(node.body[0]):
                node.body = node.body[1:] or [ast.Pass()]

    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def strip_file(src_path: str, dst_path: str) -> None:
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    out = strip_source(src)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python strip_docstrings.py <src.py> <dst.py>", file=sys.stderr)
        sys.exit(2)
    strip_file(sys.argv[1], sys.argv[2])
    print("stripped:", sys.argv[1], "->", sys.argv[2])

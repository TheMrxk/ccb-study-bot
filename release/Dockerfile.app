# ============================================================================
# 发布镜像（app）：多阶段构建
#   builder : 装 gcc + Cython，AST 剥 docstring 后把 engine.list 中的引擎编为 .so
#   runtime : 仅运行依赖 + 明文壳，引擎位置用 .so 覆盖（无引擎 .py/.pyc）
#
# 私库自用的 app/Dockerfile（明文）保留不动；本文件只服务对外发版。
# 构建上下文 = 项目根目录：
#   docker build -f release/Dockerfile.app -t ccb-study-app:dev .
# ============================================================================

# ---- builder：与 runtime 同源，保证 .so 的 ABI 可移植 ----
FROM python:3.12-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir cython setuptools wheel

WORKDIR /build

# 编译工具与引擎清单
COPY release/strip_docstrings.py   /build/tools/strip_docstrings.py
COPY release/compile_engines.py    /build/tools/compile_engines.py
COPY release/engine.list           /build/engine.list

# 引擎源码（app 全量；脚本只挑 engine.list 里的编译）
COPY app /build/app

RUN python /build/tools/compile_engines.py \
      --src   /build/app \
      --list  /build/engine.list \
      --out   /build/so_out \
      --strip /build/tools

# 列出产物以便构建日志核对
RUN find /build/so_out -name '*.so' -printf '  ENGINE %P\n' | sort


# ---- runtime：无 gcc、无 cython、无引擎明文 ----
FROM python:3.12-slim AS runtime

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 先装依赖（利用层缓存）
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 明文壳：拷入 app 全部内容……
COPY app/ .
# 引擎清单（删除引擎明文时要用，需在删除步骤之前就位）
COPY release/engine.list /release_engine.list

# ……随即删除引擎 .py 及其字节码，再用 .so 覆盖（同名同位置）
RUN set -eux; \
    while read -r rel; do \
      case "$rel" in ''|\#*) continue;; esac; \
      rm -f "$rel"; \
      rm -rf __pycache__ "$(dirname "$rel")/__pycache__"; \
    done < /release_engine.list

COPY --from=builder /build/so_out/services/ ./services/

# 安全自检：引擎位置不得残留任何 .py / .pyc（构建期即失败，而非带病发版）
RUN set -eux; \
    while read -r rel; do \
      case "$rel" in ''|\#*) continue;; esac; \
      base="${rel%.py}"; \
      if ls "${base}".py 2>/dev/null; then echo "残留明文引擎: $rel" >&2; exit 1; fi; \
      if ls "$(dirname "$rel")"/__pycache__/"$(basename "$base")"*.pyc 2>/dev/null; then \
        echo "残留引擎字节码: $rel" >&2; exit 1; fi; \
      if ! ls "${base}"*.so 2>/dev/null | grep -q .; then echo "缺少引擎 .so: $rel" >&2; exit 1; fi; \
      echo "  OK engine -> $(ls "${base}"*.so)"; \
    done < /release_engine.list

EXPOSE 8090
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]

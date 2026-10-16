#!/usr/bin/env python3
"""强严格脱敏扫描：在任何文件进入公开产物之前拦截敏感内容。

两种防护：
  1) 文件级禁发（文件名/扩展名/目录）——.env、*.db、probe_* 调试脚本等，出现即拦；
  2) 内容级正则——账号、手机号、票据、邮箱、密令实值、真实业务ID、内网IP。

用法：
    python scan_sensitive.py <待扫描目录> [--json report.json]
退出码：0 = 干净可发布；1 = 命中阻断；2 = 用法/IO 错误。

设计为只依赖标准库，可在宿主或 CI 直接跑，也可被 build_release 当库调用。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1) 文件级禁发规则
# ---------------------------------------------------------------------------

# 任何位置都不得出现的文件名（精确名）
FORBIDDEN_NAMES = {
    ".env",
    "sensitive_words.txt",   # 私有身份词表，绝不随公开壳分发
}

# 任何位置都不得出现的后缀（小写）
FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".bak",
    ".pem", ".key", ".p12", ".pfx", ".crt-origin",
}

# 任意目录下命中这些 glob 即禁发（调试/运维脚本，逆向过程产物）
FORBIDDEN_GLOBS = [
    "probe_*.py", "research_*.py", "debug_*.py", "grab_*.py",
    "verify_*.py", "explore.py", "find_*.py", "list_*.py",
    "open_*.py", "test_*.py", "confirm_*.py", "play_*.py",
    # app 下的一次性运维/修复脚本（不对外）
    "fix_*.py", "recheck_*.py", "check_*.py",
]

# 进入内容扫描的扩展名白名单（其余文件类型既不复制也不扫描，由编排层决定）
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".js", ".html", ".css", ".json", ".yml", ".yaml",
    ".ini", ".toml", ".cfg", ".conf", ".sh", ".example", ".list", ".env",
    ".webmanifest", ".csv", ".xml", ".sql",
}

# ---------------------------------------------------------------------------
# 2) 内容级规则。每条：(规则ID, 严重级别, 名称, 正则, 脱敏函数可选)
# ---------------------------------------------------------------------------

# 邮箱放行域/片段（占位、文档示例、GitHub noreply 等不算泄密）
EMAIL_ALLOW = ("example.", "your-domain", "you@", "your@", "test@", "noreply",
               "domain.com", "email.com", "user@")

# 密令赋值放行：值里出现这些说明是占位/环境变量引用，不是实值
SECRET_VALUE_ALLOW = ("${", "{{", "%", "你的", "xxxx", "XXX", "example",
                      "placeholder", "changeme", "<", ">", "getenv", "environ",
                      "Field(", "settings", "None", "''", '""')


def _mask(match_text: str) -> str:
    """命中片段脱敏：保留前2位，其余打码。"""
    t = match_text.strip()
    if len(t) <= 3:
        return t[0] + "***"
    return t[:2] + "***" + t[-1] if len(t) > 4 else t[:2] + "***"


CONTENT_RULES = [
    (
        "PHONE-CN", "block", "手机号",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ),
    (
        "CCB-TICKET", "block", "登录票据 ticket-2-*",
        re.compile(r"ticket-2-[A-Za-z0-9]{4,}"),
    ),
    (
        "EMAIL", "block", "邮箱地址",
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ),
    (
        "SECRET-LITERAL", "block", "密令硬编码实值",
        re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)"
                   r"\s*[:=]\s*['\"]([^'\"\n]{3,})['\"]", re.IGNORECASE),
    ),
    (
        "CCB-BIZ-ID", "block", "真实业务ID(microPageId/campId)",
        re.compile(r"\b(?:microPageId|campId|CAMP_ID|micro_page_id)\s*[\"']?"
                   r"\s*[:=]?\s*[\"']?\d{10,}", re.IGNORECASE),
    ),
    (
        "PRIVATE-IP", "warn", "内网/机房IP",
        re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
                   r"|192\.168\.\d{1,3}\.\d{1,3}"
                   r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    ),
]

# 身份/真实账号词表不放源码（扫描器本身要随公开仓发布，硬编码会自曝且自指阻断），
# 运行时从私有词表 sensitive_words.txt 加载（见 load_identity_patterns）。
DEFAULT_WORDS_FILE = "sensitive_words.txt"
_identity_patterns: list[re.Pattern] = []
_words_loaded = False


def load_identity_patterns(words_path: Path, *, required: bool = False) -> int:
    """加载私有敏感词表为正则列表。required=True 时缺失即抛错（防检查被静默绕过）。"""
    global _identity_patterns, _words_loaded
    if not words_path.exists():
        if required:
            raise SystemExit(
                f"❌ 缺少私有敏感词表: {words_path}（身份隔离检查不可跳过）")
        _identity_patterns, _words_loaded = [], True
        return 0
    pats = []
    for line in words_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pats.append(re.compile(line, re.IGNORECASE))
        except re.error:
            raise SystemExit(f"敏感词表存在非法正则: {line}")
    _identity_patterns, _words_loaded = pats, True
    return len(pats)



@dataclasses.dataclass
class Finding:
    kind: str          # "file" 或 "content"
    rule_id: str
    severity: str      # block / warn
    name: str
    path: str
    line: int | None = None
    snippet: str = ""

    def as_dict(self):
        return dataclasses.asdict(self)


def _forbidden_file(rel: str, name: str, suffix: str) -> str | None:
    """返回命中的文件级禁发原因，否则 None。"""
    if name in FORBIDDEN_NAMES:
        return "禁发文件名"
    if suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"禁发后缀 {suffix}"
    # git 目录
    if ".git" in Path(rel).parts:
        return ".git 目录"
    from fnmatch import fnmatch
    base = name
    for pat in FORBIDDEN_GLOBS:
        if fnmatch(base, pat):
            return f"调试/运维脚本匹配 {pat}"
    return None


def _scan_content(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        for rule_id, sev, name, rx in CONTENT_RULES:
            for m in rx.finditer(raw_line):
                hit = m.group(0)
                # 白名单放行
                if rule_id == "EMAIL" and any(a.lower() in hit.lower()
                                              for a in EMAIL_ALLOW):
                    continue
                if rule_id == "SECRET-LITERAL":
                    val = m.group(1) if m.groups() else ""
                    if any(a.lower() in val.lower() for a in SECRET_VALUE_ALLOW):
                        continue
                out.append(Finding(
                    kind="content", rule_id=rule_id, severity=sev, name=name,
                    path=rel, line=idx, snippet=_mask(hit),
                ))
        # 私有身份/账号词表（运行时加载；公开壳中通常为空）
        for rx in _identity_patterns:
            m = rx.search(raw_line)
            if m:
                out.append(Finding(
                    kind="content", rule_id="IDENTITY-LINK", severity="block",
                    name="真实身份/关联项目标识", path=rel, line=idx,
                    snippet=_mask(m.group(0)),
                ))
    return out


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        suffix = path.suffix
        name = path.name

        reason = _forbidden_file(rel, name, suffix)
        if reason:
            findings.append(Finding(
                kind="file", rule_id="FORBIDDEN-FILE", severity="block",
                name=reason, path=rel,
            ))
            continue

        if suffix.lower() not in TEXT_SUFFIXES:
            continue  # 非文本白名单：编排层默认不复制，这里不判内容
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(_scan_content(rel, text))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="强严格脱敏扫描")
    ap.add_argument("root", help="待扫描目录（通常是公开产物目录）")
    ap.add_argument("--json", dest="json_out", help="把报告写入 JSON 文件")
    ap.add_argument("--words", help="私有敏感词表路径（默认脚本同目录 sensitive_words.txt）")
    ap.add_argument("--require-words", action="store_true",
                    help="词表缺失时报错而非跳过（发版时必加，防身份检查被绕过）")
    ap.add_argument("--strict-warn", action="store_true",
                    help="warn 级也按阻断处理（默认只阻断 block）")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"[scan] 不是目录: {root}", file=sys.stderr)
        return 2

    words_path = Path(args.words) if args.words else Path(__file__).with_name(DEFAULT_WORDS_FILE)
    n = load_identity_patterns(words_path, required=args.require_words)
    if n:
        print(f"[scan] 已加载私有敏感词 {n} 条: {words_path.name}")

    findings = scan_tree(root)
    blocks = [f for f in findings if f.severity == "block"]
    warns = [f for f in findings if f.severity == "warn"]

    for f in findings:
        tag = "BLOCK" if f.severity == "block" else "warn "
        loc = f"{f.path}" + (f":{f.line}" if f.line else "")
        extra = f" -> {f.snippet}" if f.snippet else ""
        print(f"[{tag}] {f.rule_id:16} {f.name}  {loc}{extra}")

    print(f"\n[scan] 命中 {len(blocks)} 阻断 / {len(warns)} 警告，扫描根: {root}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if blocks or (args.strict_warn and warns):
        print("[scan] ❌ 未通过脱敏，禁止发版。", file=sys.stderr)
        return 1
    if warns:
        print("[scan] ⚠️  无阻断项，但有警告，请人工确认。")
    else:
        print("[scan] ✅ 干净，可发版。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

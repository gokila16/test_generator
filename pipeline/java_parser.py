"""
Parse Java source files to extract method bodies, Javadoc, and usage snippets.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from .config import MAX_SNIPPETS, SRC_ROOT
from .models import ExtractionStats, MethodEntry, ParsedName, UsageSnippet

# Matches modifiers/return-types that appear before a method name declaration.
_DECL_KEYWORDS = re.compile(
    r"\b(public|protected|private|static|final|abstract|synchronized|native|"
    r"default|void|boolean|byte|char|short|int|long|float|double|"
    r"String|List|Map|Set|Collection|Object|Iterator|Optional|[A-Z]\w*)\b"
)


# ---------------------------------------------------------------------------
# File cache
# ---------------------------------------------------------------------------

class JavaSourceCache:
    """Lazy, read-through cache for Java source file lines."""

    def __init__(self) -> None:
        self._cache: dict[Path, list[str]] = {}

    def get(self, path: Path) -> list[str]:
        if path not in self._cache:
            self._cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        return self._cache[path]


# ---------------------------------------------------------------------------
# Name parsing & file resolution
# ---------------------------------------------------------------------------

def parse_full_name(full_name: str) -> ParsedName:
    """
    Split an Understand fully-qualified name into package, class hierarchy,
    and method name using Java naming conventions (packages are lowercase,
    class names start with uppercase).
    """
    parts = full_name.split(".")
    method_name = parts[-1]

    pkg_parts, class_parts = [], []
    for seg in parts[:-1]:
        if class_parts or (seg and seg[0].isupper()):
            class_parts.append(seg)
        else:
            pkg_parts.append(seg)

    return ParsedName(
        package=".".join(pkg_parts),
        outer_class=class_parts[0] if class_parts else "",
        inner_classes=class_parts[1:],
        method_name=method_name,
    )


def resolve_source_file(parsed: ParsedName, src_root: Path) -> Path | None:
    """
    Return the outer-class .java file path, or None if not found.
    Inner-class methods live inside their outer class file.
    """
    if not parsed.outer_class:
        return None
    pkg_dir = Path(*parsed.package.split(".")) if parsed.package else Path()
    candidate = src_root / pkg_dir / f"{parsed.outer_class}.java"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Source-level extraction
# ---------------------------------------------------------------------------

def find_declaration_indices(
    lines: list[str], method_name: str, inner_class: str | None = None
) -> list[int]:
    """Return line indices of all declarations of method_name in the file."""
    name_re = re.compile(r"(?<!\w)" + re.escape(method_name) + r"\s*\(")
    candidates = []

    for i, line in enumerate(lines):
        if line.strip().startswith(("//", "*", "/*")):
            continue
        m = name_re.search(line)
        if not m:
            continue
        if not _DECL_KEYWORDS.search(line[: m.start()]):
            continue
        if re.search(r"\w\." + re.escape(method_name) + r"\s*\(", line):
            continue
        candidates.append(i)

    if inner_class and candidates:
        scoped = [i for i in candidates if _is_inside_class(lines, i, inner_class)]
        if scoped:
            return scoped

    return candidates


def extract_body(lines: list[str], sig_idx: int) -> tuple[str, str | None]:
    """
    Extract the method signature and body starting at sig_idx.
    Returns (signature, body); body is None for abstract/interface methods.
    """
    sig_lines: list[str] = []
    body_start = -1

    for i in range(sig_idx, len(lines)):
        sig_lines.append(lines[i].rstrip())
        if "{" in lines[i]:
            body_start = i
            break
        if ";" in lines[i]:
            return "\n".join(sig_lines).strip(), None

    if body_start == -1:
        return "", None

    depth, body_lines = 0, []
    for j in range(body_start, len(lines)):
        body_lines.append(lines[j].rstrip())
        for ch in _visible_chars(lines[j]):
            depth += (ch == "{") - (ch == "}")
        if depth == 0:
            break
    else:
        return "", None

    return "\n".join(sig_lines).strip(), "\n".join(body_lines)


def extract_javadoc(lines: list[str], sig_idx: int) -> str | None:
    """Return the Javadoc block immediately before sig_idx, or None."""
    i = sig_idx - 1
    while i >= 0 and (not lines[i].strip() or lines[i].strip().startswith("@")):
        i -= 1

    if i < 0 or not lines[i].strip().endswith("*/"):
        return None

    end = i
    while i >= 0 and "/**" not in lines[i]:
        i -= 1

    if i < 0 or "/**" not in lines[i]:
        return None

    return "\n".join(l.rstrip() for l in lines[i : end + 1]).strip()


def find_usage_snippets(
    method_name: str, exclude_file: Path, src_root: Path, cache: JavaSourceCache
) -> list[UsageSnippet]:
    """
    Find up to MAX_SNIPPETS call sites of method_name in src_root,
    excluding exclude_file and any test directories.
    """
    call_re = re.compile(r"(?<![a-zA-Z0-9_])" + re.escape(method_name) + r"\s*\(")
    exclude_abs = exclude_file.resolve()
    snippets: list[UsageSnippet] = []

    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if "test" not in d.lower()]
        for fname in filenames:
            if not fname.endswith(".java"):
                continue
            fpath = Path(dirpath) / fname
            if fpath.resolve() == exclude_abs:
                continue
            try:
                file_lines = cache.get(fpath)
            except OSError:
                continue
            for i, line in enumerate(file_lines):
                if line.strip().startswith(("//", "*")) or not call_re.search(line):
                    continue
                context = "".join(file_lines[max(0, i - 1) : i + 3]).rstrip()
                snippets.append(UsageSnippet(
                    file=str(fpath.relative_to(src_root)),
                    snippet=context,
                ))
                if len(snippets) >= MAX_SNIPPETS:
                    return snippets

    return snippets


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def extract_all_metadata(
    public_methods: list[dict[str, str]],
    src_root: Path,
) -> tuple[list[MethodEntry], ExtractionStats]:
    """Extract metadata for every method and return entries + aggregate stats."""
    cache = JavaSourceCache()
    occurrence_counter: dict[tuple[str, str], int] = defaultdict(int)
    stats = ExtractionStats()
    entries: list[MethodEntry] = []

    for idx, row in enumerate(public_methods, 1):
        if idx % 100 == 0:
            print(f"  {idx} / {len(public_methods)}")
        entry = _extract_one(row["Name"], row["Kind"], src_root, cache, occurrence_counter)
        stats.record(entry)
        entries.append(entry)

    return entries, stats


def _extract_one(
    full_name: str,
    kind: str,
    src_root: Path,
    cache: JavaSourceCache,
    occurrence_counter: dict[tuple[str, str], int],
) -> MethodEntry:
    parsed = parse_full_name(full_name)
    file_path = resolve_source_file(parsed, src_root)

    def failed(status: str) -> MethodEntry:
        return MethodEntry(
            full_name=full_name, class_name=parsed.immediate_class,
            inner_class=parsed.inner_class, method_name=parsed.method_name,
            file_path=str(file_path) if file_path else "",
            signature="", javadoc=None, body="", usage_snippets=[],
            kind=kind, status=status,
        )

    if not file_path:
        return failed("FILE_NOT_FOUND")

    try:
        lines = cache.get(file_path)
    except OSError:
        return failed("FILE_NOT_FOUND")

    occurrences = find_declaration_indices(lines, parsed.method_name, parsed.inner_class)
    if not occurrences:
        return failed("BODY_NOT_FOUND")

    occ_key = (str(file_path), full_name)
    occ_idx = occurrence_counter[occ_key]
    occurrence_counter[occ_key] += 1
    sig_line = occurrences[min(occ_idx, len(occurrences) - 1)]

    signature, body = extract_body(lines, sig_line)
    if not signature and not body:
        return failed("BODY_NOT_FOUND")

    return MethodEntry(
        full_name=full_name,
        class_name=parsed.immediate_class,
        inner_class=parsed.inner_class,
        method_name=parsed.method_name,
        file_path=str(file_path),
        signature=(signature or "").strip(),
        javadoc=extract_javadoc(lines, sig_line),
        body=(body or "").strip(),
        usage_snippets=find_usage_snippets(parsed.method_name, file_path, src_root, cache),
        kind=kind,
        status="OK",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_inside_class(lines: list[str], from_idx: int, class_name: str) -> bool:
    pattern = re.compile(r"\bclass\s+" + re.escape(class_name) + r"\b")
    return any(pattern.search(lines[i]) for i in range(from_idx, -1, -1))


def _visible_chars(line: str):
    """Yield characters outside string literals and line comments (for brace counting)."""
    in_str = in_char = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" : i += 2; continue
            if ch == '"': in_str = False
        elif in_char:
            if ch == "\\": i += 2; continue
            if ch == "'": in_char = False
        elif ch == '"':  in_str = True
        elif ch == "'":  in_char = True
        elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return
        else:
            yield ch
        i += 1

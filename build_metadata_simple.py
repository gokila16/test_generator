"""
build_metadata_simple.py

Produces extracted_metadata_simple.json — a metadata file in the
extracted_metadata_final.json schema, populated for every entry in
testable_methods_simple.json (3,723 simple-bucket rows still flagged
"yet to be generated").

For each bucket row this script:
  - Parses longname into (class_name_simple, inner_class, method_name)
  - Resolves file_path by walking SOURCE_DIRS for <package>/<outer>.java
  - Reads source_file_imports from the resolved .java file
  - Looks up the method's entity in the Understand .und DB and walks Call
    refs to populate dependency_signatures with high-quality, real method
    references (class_name, full_name, signature, javadoc, kind)
  - Sets has_developer_tests=False / developer_test_paths=[] / usage_snippets=[]

Run with the SciTools-bundled Python:
    "C:\\Program Files\\SciTools\\bin\\pc-win64\\upython.exe" build_metadata_simple.py
"""

import json
import os
import re
from pathlib import Path

import understand


# --- Configuration ---------------------------------------------------------

BASE = Path(r"C:\Users\Harini\Documents\thesis_research")

SIMPLE_LIST = BASE / "test_generator" / "testable_methods_simple.json"
OUTPUT      = BASE / "test_generator" / "extracted_metadata_simple.json"
DB_PATH     = BASE / "PDFBOX-v5" / "pdfbox" / "src" / "main" / "main.und"

SOURCE_DIRS = [
    BASE / "PDFBOX-v5" / "pdfbox"           / "src" / "main" / "java",
    BASE / "PDFBOX-v5" / "pdfbox-io"        / "src" / "main" / "java",
    BASE / "PDFBOX-v5" / "pdfbox-rendering" / "src" / "main" / "java",
]

MAX_DEPS_PER_METHOD = 200


# --- Helpers ---------------------------------------------------------------

def _norm_params(sig_or_params: str) -> str:
    if not sig_or_params:
        return ""
    m = re.search(r"\((.*?)\)", sig_or_params, re.DOTALL)
    inside = m.group(1) if m else sig_or_params
    inside = inside.strip()
    if not inside:
        return ""
    inside = re.sub(r"\s+", " ", inside)
    parts, depth, cur = [], 0, ""
    for ch in inside:
        if ch == "<": depth += 1
        elif ch == ">": depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip()); cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    types = []
    for p in parts:
        toks = p.split()
        types.append(" ".join(toks[:-1]) if len(toks) > 1 else p)
    return ",".join(types)


def _signature_from_contents(contents: str) -> str:
    if not contents:
        return ""
    idx_brace = contents.find("{")
    idx_semi  = contents.find(";")
    candidates = [i for i in (idx_brace, idx_semi) if i != -1]
    if not candidates:
        return contents.strip()
    return contents[: min(candidates)].strip()


def _parse_longname(longname: str, package: str):
    method_name = longname.rsplit(".", 1)[-1]
    fqn_class = longname[: -(len(method_name) + 1)]
    if package and fqn_class.startswith(package + "."):
        class_part = fqn_class[len(package) + 1:]
    else:
        class_part = fqn_class
    parts = class_part.split(".")
    outer = parts[0] if parts else ""
    inner_classes = parts[1:]
    immediate = inner_classes[-1] if inner_classes else outer
    inner = inner_classes[-1] if inner_classes else None
    return outer, immediate, inner, method_name


def _resolve_source_file(package: str, outer_class: str):
    if not outer_class:
        return None
    pkg_dir = Path(*package.split(".")) if package else Path()
    for root in SOURCE_DIRS:
        candidate = root / pkg_dir / f"{outer_class}.java"
        if candidate.exists():
            return candidate
    return None


def _extract_imports(file_path):
    if not file_path or not file_path.exists():
        return []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    imports = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        if s.startswith("package "):
            continue
        if s.startswith("import "):
            target = s[len("import "):].rstrip(";").strip()
            if target:
                imports.append(target)
            continue
        if s.startswith(("public ", "final ", "abstract ", "class ",
                         "interface ", "enum ", "@")):
            break
    return imports


_DECL_KW_RE = re.compile(
    r"\b(public|protected|private|static|final|abstract|synchronized|native|"
    r"default|void|boolean|byte|char|short|int|long|float|double|"
    r"String|List|Map|Set|Collection|Object|Iterator|Optional|[A-Z]\w*)\b"
)


def _find_decl_index(lines, method_name: str):
    name_re = re.compile(r"(?<!\w)" + re.escape(method_name) + r"\s*\(")
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(("//", "*", "/*")):
            continue
        m = name_re.search(line)
        if not m:
            continue
        if not _DECL_KW_RE.search(line[: m.start()]):
            continue
        if re.search(r"\w\." + re.escape(method_name) + r"\s*\(", line):
            continue
        return i
    return -1


def _extract_javadoc_from_source(file_path, method_name):
    if not file_path or not file_path.exists():
        return None
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    sig_idx = _find_decl_index(lines, method_name)
    if sig_idx <= 0:
        return None
    i = sig_idx - 1
    while i >= 0 and (not lines[i].strip() or lines[i].strip().startswith("@")):
        i -= 1
    if i < 0 or not lines[i].rstrip().endswith("*/"):
        return None
    end = i
    while i >= 0 and "/**" not in lines[i]:
        i -= 1
    if i < 0:
        return None
    return "\n".join(l.rstrip() for l in lines[i:end + 1]).strip()


# --- Main ------------------------------------------------------------------

def main() -> None:
    print(f"Loading {SIMPLE_LIST}")
    with SIMPLE_LIST.open(encoding="utf-8") as f:
        simple = json.load(f)
    print(f"  {len(simple)} entries\n")

    print(f"Opening Understand DB: {DB_PATH}")
    db = understand.open(str(DB_PATH))

    print("Indexing method/constructor entities...")
    ent_index = {}
    for e in db.ents("method ~unknown ~unresolved, constructor"):
        ent_index[(e.longname(), _norm_params(e.parameters() or ""))] = e
    print(f"  {len(ent_index)} entities indexed.\n")

    out = []
    matched_und = 0
    not_in_db = 0
    deps_total = 0
    imports_filled = 0
    file_resolved = 0

    for i, b in enumerate(simple, 1):
        if i % 250 == 0:
            print(f"  {i}/{len(simple)}")

        package  = b.get("package", "") or ""
        longname = b.get("longname", "") or ""
        outer, immediate, inner, method_name = _parse_longname(longname, package)

        file_path = _resolve_source_file(package, outer)
        file_resolved += 1 if file_path else 0
        file_path_str = str(file_path) if file_path else ""

        imports = _extract_imports(file_path) if file_path else []
        if imports:
            imports_filled += 1

        # Signature + body come straight from the bucket entry, but the
        # bucket schema includes the declaration line in `body`; strip it
        # so `body` matches the extracted_metadata schema.
        bucket_body = (b.get("body") or "").strip()
        # If body starts with anything other than "{", treat the line(s) up
        # to the first "{" as the declaration and split.
        body = bucket_body
        if body and not body.lstrip().startswith("{"):
            brace = body.find("{")
            if brace != -1:
                body = body[brace:].strip()

        javadoc = _extract_javadoc_from_source(file_path, method_name) if file_path else None

        # dependency_signatures via Understand
        deps = []
        key = (longname, _norm_params(b.get("signature", "")))
        ent = ent_index.get(key)
        if ent is None:
            not_in_db += 1
        else:
            matched_und += 1
            seen = set()
            for ref in ent.refs("call"):
                callee = ref.ent()
                if callee is None:
                    continue
                ck = (callee.longname(), _norm_params(callee.parameters() or ""))
                if ck in seen:
                    continue
                seen.add(ck)
                parent = callee.parent()
                sig_from_src = _signature_from_contents(callee.contents() or "")
                if not sig_from_src:
                    ret = callee.type() or ""
                    params = callee.parameters() or ""
                    sig_from_src = f"{ret} {callee.name()}({params})".strip()
                jd_blocks = callee.comments("before", "javadoc") or []
                jd = jd_blocks[0].strip() if jd_blocks else None
                deps.append({
                    "class_name": parent.name() if parent else "",
                    "full_name":  callee.longname(),
                    "signature":  sig_from_src,
                    "javadoc":    jd,
                    "kind":       callee.kindname(),
                })
                if len(deps) >= MAX_DEPS_PER_METHOD:
                    break
        deps_total += len(deps)

        out.append({
            "full_name":             longname,
            "class_name":            immediate,
            "inner_class":           inner,
            "method_name":           method_name,
            "file_path":             file_path_str,
            "signature":             b.get("signature", ""),
            "javadoc":               javadoc,
            "body":                  body,
            "usage_snippets":        [],
            "kind":                  b.get("kind", ""),
            "status":                "OK" if file_path else "FILE_NOT_FOUND",
            "dependency_signatures": deps,
            "developer_test_paths":  [],
            "has_developer_tests":   False,
            "source_file_imports":   imports,
        })

    db.close()

    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("\n========== SUMMARY ==========")
    print(f"  Total simple entries           : {len(simple)}")
    print(f"  File resolved (status=OK)      : {file_resolved}")
    print(f"  Matched to .und entity         : {matched_und}")
    print(f"  Not in DB (out of scope)       : {not_in_db}")
    print(f"  With source_file_imports       : {imports_filled}")
    print(f"  Total dependency edges         : {deps_total}")
    if matched_und:
        print(f"  Avg deps per matched entry     : {deps_total/matched_und:.1f}")
    print(f"  Output                         : {OUTPUT}")
    print("==============================")


if __name__ == "__main__":
    main()

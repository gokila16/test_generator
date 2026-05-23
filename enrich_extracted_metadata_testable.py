"""
enrich_extracted_metadata_testable.py

Reads PDFBOX-v5/testable_methods_extracted_metadata.json (output of
extract_for_testable_methods.py — 11 fields per entry) and produces a fully
enriched copy that matches the extracted_metadata_final.json schema (15 fields).

New fields added:
  - source_file_imports       (parsed from each method's source file)
  - dependency_signatures     (Understand-derived; class_name, full_name,
                               signature, javadoc, kind for every callee)
  - has_developer_tests       (defaults to False — pipeline filters off this)
  - developer_test_paths      (defaults to [])

Output:
  C:\\Users\\Harini\\Documents\\thesis_research\\test_generator\\extracted_metadata_testable.json

This script must be run with the bundled SciTools Python (which has the
`understand` module on its path):

    "C:\\Program Files\\SciTools\\bin\\pc-win64\\upython.exe" enrich_extracted_metadata_testable.py
"""

import json
import re
from pathlib import Path
import understand


# --- Configuration ---------------------------------------------------------

BASE = Path(r"C:\Users\Harini\Documents\thesis_research")

INPUT  = BASE / "PDFBOX-v5" / "testable_methods_extracted_metadata.json"
OUTPUT = BASE / "test_generator" / "metadata.json"

DB_PATH = BASE / "PDFBOX-v5" / "pdfbox" / "src" / "main" / "main_full.und"

# Same source roots used by build_class_inventory.py
SOURCE_DIRS = [
    BASE / "PDFBOX-v5" / "pdfbox"           / "src" / "main" / "java",
    BASE / "PDFBOX-v5" / "pdfbox-io"        / "src" / "main" / "java",
    BASE / "PDFBOX-v5" / "pdfbox-rendering" / "src" / "main" / "java",
]

MAX_DEPS_PER_METHOD = 200


# --- Param normalization (used to match metadata <-> Understand entities) --

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
    return contents[:min(candidates)].strip()


def _build_member_entry(member, class_name: str) -> dict:
    """Render one method/constructor entity as a dependency_signatures row."""
    sig_from_src = _signature_from_contents(member.contents() or "")
    if not sig_from_src:
        ret = member.type() or ""
        params = member.parameters() or ""
        sig_from_src = f"{ret} {member.name()}({params})".strip()
    jd_blocks = member.comments("before", "javadoc") or []
    return {
        "class_name": class_name,
        "full_name":  member.longname(),
        "signature":  sig_from_src,
        "javadoc":    jd_blocks[0].strip() if jd_blocks else None,
        "kind":       member.kindname(),
    }


# --- source_file_imports extraction ---------------------------------------

def _extract_imports(file_path: Path) -> list:
    if not file_path or not file_path.exists():
        return []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    imports: list = []
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


def _resolve_file_path(entry: dict) -> Path | None:
    """Honour an existing file_path; otherwise resolve from package + outer class."""
    fp = entry.get("file_path")
    if fp:
        p = Path(fp)
        if p.exists():
            return p
    full_name = entry.get("full_name", "")
    if not full_name:
        return None
    method_name = entry.get("method_name") or full_name.rsplit(".", 1)[-1]
    fqn_class = full_name[: -(len(method_name) + 1)] if method_name else full_name
    parts = fqn_class.split(".")
    pkg_parts, class_parts = [], []
    for seg in parts:
        if class_parts or (seg and seg[0].isupper()):
            class_parts.append(seg)
        else:
            pkg_parts.append(seg)
    if not class_parts:
        return None
    pkg_dir = Path(*pkg_parts) if pkg_parts else Path()
    outer = class_parts[0]
    for root in SOURCE_DIRS:
        candidate = root / pkg_dir / f"{outer}.java"
        if candidate.exists():
            return candidate
    return None


# --- Main ------------------------------------------------------------------

def main() -> None:
    print(f"Input : {INPUT}")
    print(f"Output: {OUTPUT}")
    print(f"DB    : {DB_PATH}\n")

    with INPUT.open(encoding="utf-8") as f:
        entries = json.load(f)
    print(f"Loaded {len(entries)} entries\n")

    # ---- Pass 1: index all methods/constructors in the .und DB ----
    print("Opening Understand DB and indexing entities...")
    db = understand.open(str(DB_PATH))
    ent_index = {}
    for e in db.ents("method ~unknown ~unresolved, constructor"):
        ent_index[(e.longname(), _norm_params(e.parameters() or ""))] = e
    print(f"  {len(ent_index)} method/constructor entities indexed.\n")

    # ---- Pass 2: enrich each entry ----
    print("Enriching entries...")
    matched = 0
    not_in_db = 0
    deps_total = 0
    imports_filled = 0

    file_path_cache: dict = {}

    for i, entry in enumerate(entries, 1):
        if i % 50 == 0:
            print(f"  {i}/{len(entries)}")

        # --- source_file_imports ---
        fp = file_path_cache.get(entry.get("file_path"))
        if fp is None:
            fp = _resolve_file_path(entry)
            if entry.get("file_path"):
                file_path_cache[entry["file_path"]] = fp
        imports = _extract_imports(fp) if fp else []
        entry["source_file_imports"] = imports
        if imports:
            imports_filled += 1

        # --- dependency_signatures ---
        # Broadened query (Tier 2): `call` (method/constructor invocations) +
        # `create` (defensive; constructor refs that Understand may classify
        # separately) + `type` (declared return/param/var types) + `throw`
        # (exception types) + `dotref` (static class references that don't
        # end in a method call). Each ref is then branched on the callee's
        # kindname: methods/constructors are recorded under their declaring
        # class; class/interface/enum entities trigger a one-time expansion
        # of that class's public constructors so the LLM knows how to
        # instantiate it even when the body never does `new X(...)` itself.
        # Noise filter: skip callees with no resolvable parent class (catches
        # JDK stdlib calls like Map.values(), Stream.distinct() that
        # Understand surfaces but can't attribute).
        key = (entry.get("full_name", ""), _norm_params(entry.get("signature", "")))
        ent = ent_index.get(key)
        deps: list = []
        if ent is None:
            not_in_db += 1
        else:
            matched += 1
            seen_members = set()   # (longname, normalized-params) for methods/constructors
            seen_classes = set()   # longname for class-level expansion

            for ref in ent.refs("call, create, type, throw, dotref"):
                callee = ref.ent()
                if callee is None:
                    continue
                kind = callee.kindname().lower()

                if "method" in kind or "constructor" in kind:
                    parent = callee.parent()
                    class_name = parent.name() if parent else ""
                    if not class_name:
                        continue  # noise filter: drop stdlib-ish unresolved callees
                    ck = (callee.longname(), _norm_params(callee.parameters() or ""))
                    if ck in seen_members:
                        continue
                    seen_members.add(ck)
                    deps.append(_build_member_entry(callee, class_name))

                elif ("class" in kind or "interface" in kind or "enum" in kind) \
                        and "unknown" not in kind and "unresolved" not in kind:
                    class_long = callee.longname()
                    if class_long in seen_classes:
                        continue
                    seen_classes.add(class_long)
                    for ctor in callee.ents("define", "constructor ~private"):
                        ck = (ctor.longname(), _norm_params(ctor.parameters() or ""))
                        if ck in seen_members:
                            continue
                        seen_members.add(ck)
                        deps.append(_build_member_entry(ctor, callee.name()))
                        if len(deps) >= MAX_DEPS_PER_METHOD:
                            break

                if len(deps) >= MAX_DEPS_PER_METHOD:
                    break

        entry["dependency_signatures"] = deps
        deps_total += len(deps)

        # --- coverage flags (filter-only fields) ---
        entry.setdefault("developer_test_paths", [])
        entry.setdefault("has_developer_tests", False)

    db.close()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    print("\n========== SUMMARY ==========")
    print(f"  Total entries                   : {len(entries)}")
    print(f"  Matched to .und entity          : {matched}")
    print(f"  Not in DB (out of scope)        : {not_in_db}")
    print(f"  Entries with source_file_imports: {imports_filled}")
    print(f"  Total dependency edges set      : {deps_total}")
    if matched:
        print(f"  Avg deps per matched entry      : {deps_total/matched:.1f}")
    print(f"  Output                          : {OUTPUT}")
    print("==============================")


if __name__ == "__main__":
    main()

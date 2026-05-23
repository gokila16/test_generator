# Where `extracted_metadata_final.json` comes from

`extracted_metadata_final.json` is the **1309-method dataset** that the test generator
runs against. It is produced by a **two-script chain** (`pipeline_step1.py` →
`pipeline_step2.py`), both backed by a `pipeline/` package, plus a later **enrichment**
pass that adds the dependency/import/developer-test fields.

> Companion doc: [`methods_testable.md`](methods_testable.md) describes the *testable*
> (289) and *simple* (3723) datasets, which are a separate partition built from a
> different raw inventory. `extracted_metadata_final.json` is **not** derived from those —
> it has its own pipeline, described here.

## Source

The raw source is the **PDFBox `src/main/java` tree**, statically indexed by
**SciTools Understand**. From `pipeline/config.py`:

- `UND_DB`   → `pdfbox/src/main/main.und`  — the Understand database
- `SRC_ROOT` → `pdfbox/src/main/java`      — where bodies / javadoc / imports are read from

Nothing is hand-curated: the method list is whatever Understand reports for the main
(non-test) Java code.

## Pipeline overview

```
PDFBox src/main/java  +  main.und  (SciTools Understand index)
        |
        |  pipeline_step1.py
        |    1. und analyze + und metrics
        |    2. load_public_methods()   <- SELECTION: 6 public kinds, drop name contains "Test"
        |    3. extract_all_metadata()  <- read signature / body / javadoc / usage_snippets
        v
   extracted_metadata.json            (~4416 public methods)
        |
        |  pipeline_step2.py
        |    apply_filters()            <- 3 removal rules (see below)
        |    clean_methods()            <- fix status, strip trailing "{" from signatures
        v
   extracted_metadata_final.json       (1309 methods)
        |
        |  enrichment pass (one-off; see "Caveats")
        |    + source_file_imports
        |    + dependency_signatures
        |    + has_developer_tests / developer_test_paths
        v
   extracted_metadata_final.json       (1309 methods, 15-field schema)
```

Only `pipeline_step1.py` and `pipeline_step2.py` are needed to reach the 1309 count; the
enrichment fields are layered on afterward without changing the count.

## Selection — what gets into the candidate set (Step 1)

In `pipeline/understand.py` (`load_public_methods`) a method is kept only if:

- its Understand `Kind` is one of the 6 public kinds (`PUBLIC_KINDS` in `pipeline/config.py`):
  `Public Method`, `Public Static Method`, `Public Final Method`,
  `Public Static Final Method`, `Public Generic Method`, `Public Static Generic Method`
  — this drops private / protected / package-private methods, fields, etc.
- its `Name` does **not** contain the substring `"Test"` — drops test methods.

Then `pipeline/java_parser.py` (`extract_all_metadata`) opens each declaring `.java` file
and pulls out the `signature`, `body`, `javadoc`, and `usage_snippets`.

## Filters — the three removal rules (Step 2)

Defined in `pipeline/filters.py` (`apply_filters`), applied in sequence:

| Rule | Removes |
| ---- | ------- |
| Rule 1 | `get` / `set` / `is` / `has` accessors whose body is **≤ 5 lines** |
| Rule 2 | Boilerplate by name: `toString, hashCode, equals, compareTo, clone, finalize, notify, wait` |
| Rule 3 | Any method whose body is **≤ 2 lines** |

`clean_methods()` then mutates survivors in place: entries with no body but `status == OK`
are re-tagged `BODY_NOT_FOUND`, and a trailing `{` is stripped from each signature.

> Note the difference from the *testable* pipeline: `extracted_metadata_final.json`
> contains **methods only** (no `Public Constructor` kind), which is why constructors
> appear in the testable/simple datasets but not here.

## How we end up with 1309

```
PDFBox public methods (Understand, "Test"-named dropped) : 4416
        |  Rule 1  (trivial get/set/is/has, body <= 5 lines)
        |  Rule 2  (boilerplate names)
        |  Rule 3  (body <= 2 lines)            -> 3107 removed
        v
extracted_metadata_final.json                   : 1309
```

Verified against the committed artifacts:

- `public_methods.csv` (pre-filter) = 4417 rows = 1 header + **4416** methods
- `filtered_public_methods.csv` (post-filter) = 1310 rows = 1 header + **1309** methods
- `extracted_metadata_final.json` = **1309** records, all `status == OK`, all with a body

(The per-rule removal counts are printed by `pipeline_step2.py` at runtime but are not
persisted anywhere, and the intermediate `extracted_metadata.json` is not in the repo, so
only the 4416 → 1309 total can be reconstructed.)

## The 932 vs 1309 split (developer-test coverage)

The enrichment pass tags each method with `has_developer_tests`. Of the 1309:

- **377** already have developer tests (`has_developer_tests = True`)
- **932** do not (`has_developer_tests = False`)

The generator only processes the **932 uncovered** methods:

- `src/loader.py` keeps methods where `status == OK` **and** `body` **and**
  `not has_developer_tests`.
- `build_dependency_chains.py` sets `UNCOVERED_ONLY = True` to resolve dependency chains
  for those 932 only (set it to `False` to build chains for all 1309).

This is the "932 vs 1309" referenced throughout the code.

## The 15-field schema

Each record carries:

`full_name`, `class_name`, `inner_class`, `method_name`, `file_path`, `signature`,
`javadoc`, `body`, `usage_snippets`, `kind`, `status`, `dependency_signatures`,
`source_file_imports`, `has_developer_tests`, `developer_test_paths`.

The first 11 come from Step 1/2; the last 4 (`dependency_signatures`,
`source_file_imports`, `has_developer_tests`, `developer_test_paths`) are added by the
enrichment pass.

## Caveats (reproducibility)

1. **The `pipeline/` package is no longer on disk.** It existed in the initial commit
   (`pipeline/config.py`, `filters.py`, `understand.py`, `java_parser.py`, `io.py`,
   `models.py`) but was deleted afterward. `pipeline_step1.py` / `pipeline_step2.py` still
   `import` from it, so they will **not run as-is** today. The selection/filter logic
   documented above was recovered from git history (commit `47cf02a`).

2. **No committed script writes `has_developer_tests`.** The field is present in the JSON
   and is *consumed* by `build_dependency_chains.py` and `src/loader.py`, but the
   enrichment script that *added* it (and `dependency_signatures` / `source_file_imports`)
   is not in the repo — it was a one-off / uncommitted step. So the 932/377 split exists in
   the data, but its generating code is not reproducible from the current tree. (For the
   *testable* pipeline this enrichment is done by `enrich_extracted_metadata_testable.py`;
   the equivalent for the `final` dataset was not committed.)

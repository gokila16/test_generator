# Where `extracted_metadata_testable.json` comes from

`extracted_metadata_testable.json` is produced by a **5-stage chain** that crosses two
directories (`PDFBOX-v5/` and `test_generator/`). The "filtering" happens in two distinct
places: first deciding **what counts as testable**, then deciding **which complexity bucket**
each method falls into.

## Pipeline overview

```
PDFBOX-v5/pdfboxMethods.json                         <- raw inventory of ALL methods (Understand-derived)
        |
        |  extract_testable_methods.py    <- FILTER #1: "is this testable?"
        v
   testable_methods_inventory.json
        |
        |  bucket_testable_methods.py     <- FILTER #2: complexity bucketing
        v
   testable_methods_buckets.json  (4861, each tagged test_priority_bucket)
        |
        +--> testable_methods_simple.json (3723)            -+ simple path
        +--> testable_methods_moderate_complex.json (289)   -+ testable path
                    |                                         |
   extract_for_testable_methods.py                  build_metadata_simple.py
   (re-derives body/sig/javadoc from source)        (one-pass build)
                    v                                         v
   testable_methods_extracted_metadata.json          extracted_metadata_simple.json
                    |
   enrich_extracted_metadata_testable.py
   (+source_file_imports, +dependency_signatures via Understand DB)
                    v
   extracted_metadata_testable.json   <- (289 entries)
```

## Filter #1 — what makes a method "testable"

Defined in `PDFBOX-v5/extract_testable_methods.py` (`is_testable`, line 11).
A method is kept only if **all** of the following hold:

- `kind` is in `{"Public Method", "Public Constructor"}` — this is exactly why constructors
  appear in the testable/simple datasets but **not** in `extracted_metadata_final.json`, and
  why non-public methods are dropped.
- the method is **not** abstract.
- it has a non-empty `body`.
- it has `signature`, `method`, and `class` identity fields.

## Filter #2 — bucketing by complexity

Defined in `PDFBOX-v5/bucket_testable_methods.py` (`classify_bucket`, line 22).
Using `cyclomatic_complexity`, `num_parameters`, and `lines_of_code`:

- **Constructor override**: a `Public Constructor` with cyclomatic <= 2, params <= 3,
  loc <= 25 -> **simple**
- **complex**: cyclomatic >= 6 **or** params >= 5 **or** loc >= 40
- **moderate**: cyclomatic >= 3 **or** params >= 3 **or** loc >= 20
- otherwise -> **simple**

The counts confirm the split: **`extracted_metadata_testable` (289) = the moderate + complex
buckets**, while **`extracted_metadata_simple` (3723) = the simple bucket**. So "testable" in
this naming really means *the harder methods*, and "simple" is the easy bucket — they are
complementary partitions of the same testable inventory, run through pipelines tuned to their
difficulty.

## Stages 4-5 — enrichment to the 15-field schema

- **Testable** is built in **two passes**:
  1. `extract_for_testable_methods.py` re-derives `body` / `signature` / `javadoc` /
     `usage_snippets` from the live source tree (via `pipeline.java_parser.extract_all_metadata`),
     writing the 11-field `PDFBOX-v5/testable_methods_extracted_metadata.json`.
  2. `enrich_extracted_metadata_testable.py` opens the Understand `.und` DB and adds
     `source_file_imports` and `dependency_signatures` (walking `call, create, type, throw,
     dotref` refs, and expanding public constructors of referenced classes), plus the
     `has_developer_tests = False` / `developer_test_paths = []` filter fields. This brings the
     record up to the full 15-field `extracted_metadata_final.json` schema.

- **Simple** does all of that in **one pass** (`build_metadata_simple.py`), and notably takes
  `body` straight from the bucket entry rather than re-walking the source.

## Manual rename step

`enrich_extracted_metadata_testable.py` (line 35) writes its output to **`metadata.json`**, not
`extracted_metadata_testable.json` — even though its docstring (line 16) refers to the latter.
This is intentional: the final file is produced by **manually renaming** the script's output.

After running `enrich_extracted_metadata_testable.py`, rename its output:

```
metadata.json  ->  extracted_metadata_testable.json
```

The `metadata.json` file in the working tree is this script's output prior to the rename. This
rename is a manual step and is not performed by any script, so remember to do it before pointing
the pipeline (via `config_testable.py`) at `extracted_metadata_testable.json`.

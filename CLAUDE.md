# Test Generator — System Architecture

Automated JUnit 5 test generation for Apache PDFBox using Gemini 2.5-flash via Vertex AI.
The system extracts method metadata from the PDFBox source tree, routes each method through
either a simple or complex generation path, and validates the output with static analysis
and Maven before recording results.

---

## Entry Points

| Script | Purpose |
|---|---|
| `pipeline_v5.py` | **Main pipeline.** Routes each method through the complexity classifier, then runs the simple or complex path. |
| `pipeline_step3.py` | Legacy simple-only pipeline. Still works; delegates per-method logic to `src/simple_path.py`. |
| `build_dependency_chains.py` | Pre-processing step. Reads `extracted_metadata_final.json` + `class_inventory.json`, resolves how to construct every method parameter, writes `dependency_chains.json`. Run once before the pipeline. |
| `build_class_inventory.py` | Pre-processing step. Parses the Java source tree, writes `class_inventory.json` (constructors, factories, inheritance). Run before `build_dependency_chains.py`. |

Run order for a clean setup:
```
python build_class_inventory.py
python build_dependency_chains.py
python pipeline_v5.py
```

---

## Configuration — `config.py`

All paths, LLM settings, and tuning knobs in one place.

| Key | Default | Meaning |
|---|---|---|
| `BASE_DIR` | `PDFBOX-v5/` | Root of the PDFBox project |
| `PDFBOX_DIR` | `BASE_DIR/pdfbox` | Maven module root |
| `INPUT_JSON` | `extracted_metadata_final.json` | Method metadata input |
| `DEPENDENCY_CHAINS_FILE` | `dependency_chains.json` | Pre-built construction chains |
| `CALL_GRAPH_FILE` | `call_graph.json` | Real usage examples |
| `RESULTS_JSON` | `results/results.json` | Cumulative run results |
| `LLM_MODEL` | `gemini-2.5-flash` | Gemini model via Vertex AI |
| `MAX_RETRIES` | `2` | Maven retry budget (also used by checklist/resource agents) |
| `CLC_THRESHOLD` | `5` | Methods with CLC ≥ this, or with unresolvable/file-dependent params, go through the complex path |
| `MAVEN_EXECUTABLE` | full path to `mvn.cmd` | Maven binary location |
| `JAVA_HOME` | full path to JDK | Injected into Maven subprocess environment |

---

## Pipeline Flow

```
extracted_metadata_final.json
        │
        ▼
  load_methods()           [src/loader.py]
  assign_unique_keys()     [pipeline_v5.py]
        │
        ▼
  Context lookup           [src/context_loader.py]
  dependency_chains.json + call_graph.json
        │
        ▼
  Complexity classifier    [src/complexity_classifier.py]
  compute_clc(body) + has_external_dependencies()
        │
   ┌────┴────┐
simple     complex
   │           │
   │     Checklist agent   [src/checklist_agent.py]
   │     generate_checklist()  →  validate_checklist() [≤2× internal retry]
   │           │
   │           │  CHECKLIST_FAILED → hard stop, save result
   │           │
   │     Resource generator agent  [src/resource_generator_agent.py]
   │     generate_resources()  →  validate_resources() [≤2× internal retry]
   │           │
   │           │  resource failed → strip resource_spec from checklist
   │           │                    fallback to simple path (path='complex_resource_fallback')
   │           │
   └─────┬─────┘
         ▼
   run_simple_path()        [src/simple_path.py]
   (accepts checklist=, resources=, resource_fallback=)
         │
    Step 1: planning prompt   [src/prompt_builder.py]
    Step 2: generation prompt [src/prompt_builder.py]
         │
    Static allowlist check    [src/allowlist_checker.py]
    LLM fix if violations     [≤2× internal retry]
         │
         │  ALLOWLIST_FAILED → hard stop, save result
         │
    assemble_test_package()   [src/file_manager.py]   ← only when resources present
         │
    Maven: compile + run      [src/maven_runner.py]
    LLM retry on failure      [≤ config.MAX_RETRIES]
         │
    save_result()             [src/result_tracker.py]
```

---

## Source Modules

### `src/complexity_classifier.py`
Decides whether a method goes through the simple or complex path.

- `compute_clc(body)` — strips block comments, line comments, string literals, then counts
  branching keywords (`if`, `else if`, `for`, `while`, `do`, `case`, `catch`) plus ternary `?`.
- `has_external_dependencies(method, dep_chains)` — returns True if any param or receiver in the
  dependency chain has strategy `unknown` or `unresolvable_abstract`.
- `classify(method, dep_chains, threshold)` — returns `'simple'` or `'complex'`.

### `src/checklist_agent.py`
Complex path, step 1. Two public functions with symmetric contracts.

- `generate_checklist(method, dep_chain, resource_files, caller_snippets)` — one LLM call.
  Returns parsed `dict | None`.
- `validate_checklist(checklist, method, clc)` — validates, calls fix prompt on failure,
  retries up to `MAX_RETRIES`. Returns `(bool, list[str], dict)`.

Checklist dict schema:
```python
{
  "branch_plan":   [{"name", "scenario", "branch", "expected"}, ...],
  "resource_spec": [{"type", "name", "description"}, ...],
  "input_types":   [{"name", "type", "description"}, ...]
}
```

### `src/resource_generator_agent.py`
Complex path, step 2.

- `generate_resources(method, checklist, dep_chain, resource_files)` — one LLM call.
  Returns `dict[varName → {type, construction}] | None`.
- `validate_resources(resources, method)` — builds a short-name map from `source_file_imports`,
  checks each resource type is resolvable; retries up to `MAX_RETRIES`.
  Returns `(bool, list[str], dict)`.

Resources are **in-memory only** — they are injected into the test at assembly time and not
persisted as separate files. The raw LLM response is saved to `responses/_resource_response.txt`.

### `src/simple_path.py`
Per-method generation logic used by both pipelines. Accepts optional context from the complex path.

Parameters beyond `method / dep_chains / call_graph`:
- `checklist` — injected into the planning prompt as branch guidance (stripped of `resource_spec` in
  the fallback case).
- `resources` — injected into the planning prompt as pre-built fixture context AND assembled into
  the generated test via `assemble_test_package()` before saving.
- `resource_fallback` — when True, records `path='complex_resource_fallback'` in the result.

Every result written by this function includes a `path` field:
`'simple'` | `'complex'` | `'complex_resource_fallback'`

### `src/prompt_builder.py`
All LLM prompt construction. No LLM calls here — pure string building.

| Function | Used by |
|---|---|
| `build_planning_prompt()` | Step 1 of generation (both paths). Accepts `checklist=` and `resources=` optional context. |
| `build_generation_from_plan_prompt()` | Step 2 of generation. |
| `build_allowlist_violation_prompt()` | Allowlist fix retry. |
| `build_retry_prompt()` | Maven failure retry. |
| `build_checklist_prompt()` | Checklist agent — initial generation. |
| `build_checklist_fix_prompt()` | Checklist agent — fix retry. |
| `build_resource_generation_prompt()` | Resource agent — initial generation. |
| `build_resource_fix_prompt()` | Resource agent — fix retry. |

### `src/allowlist_checker.py`
Static validation that generated test code only calls methods present in `dependency_signatures`.
Uses type inference (variable declaration parsing + return-type propagation) to resolve receiver
types before checking. JUnit assertion classes and universal Object methods are always allowed.
Returns `(passed: bool, violations: list[str])`.

### `src/maven_runner.py`
Compiles and runs a single test class via Maven subprocesses.
1. Copies the test file to `src/test/java/{package}/`.
2. Runs `compiler:testCompile`.
3. If compile passes, runs `surefire:test -Dtest={ClassName}`.
4. Deletes the test file after.
Returns `(compiled: bool, passed: bool, error_message: str)`.

### `src/file_manager.py`
File I/O helpers.

- `save_prompt(prompts_dir, full_name, prompt, ..., prompt_type=None)` — `prompt_type` string
  overrides the legacy bool flags when provided (e.g. `'checklist'`, `'resource'`).
- `save_response(responses_dir, full_name, response, ..., response_type=None)` — same pattern.
- `save_test_file()` — writes generated `.java` to `generated_tests/{package_path}/`.
- `assemble_test_package(test_code, resources, method, overload_index)` — injects resource
  construction statements into the test. If `@BeforeEach` exists, appends to its body; otherwise
  inlines each resource at the top of the `@Test` methods that reference it. Skips variables
  already declared. Returns the assembled code string.

### `src/context_loader.py`
Loads `dependency_chains.json` and `call_graph.json` once at startup.
- `get_dependency_chain(dep_chains, method)` — key: `"full_name|signature"`.
- `get_caller_snippets(call_graph, method, max_snippets=2)` — real usage examples for the prompt.

### `src/resource_scanner.py`
- `scan_test_resources(resources_dir)` — walks `src/test/resources/`, groups files by extension.
- `is_file_dependent(method)` — checks signature for `File`, `InputStream`, `RandomAccessRead`, etc.

### `src/llm_client.py`
Single `call_llm(prompt)` function. Calls Gemini 2.5-flash via Vertex AI (`vertexai=True`).
Handles 429 / RESOURCE_EXHAUSTED with a 60s wait and one retry. Returns text or None.

### `src/result_tracker.py`
Reads and writes `results.json`. `is_already_processed()` is used at the top of both pipeline
loops to skip methods that already have a result — enabling resume-on-crash.

### `src/reporter.py`
`print_final_report()` prints status counts, retry stats, total time, and (when `pipeline_v5`
has run) a path breakdown table showing how many methods went through each route and how often
the `complex_resource_fallback` path still produced a passing test.

### `src/loader.py`
Loads `extracted_metadata_final.json`, filters to methods with `status='OK'` and a non-empty body.

### `src/code_extractor.py`
Extracts Java source from an LLM response: tries fenced code block, then raw content starting
with `import` or `public class`. Returns None if nothing matches.

### `src/java_post_processor.py`
Three fixes applied to every generated test before saving:
1. Collapse two-line `URL url = ...; File f = new File(url.toURI());` into one line.
2. Inject `import java.net.URL;` if a `URL` variable still appears after step 1.
3. Add `throws Exception` to every `@Test` method that lacks a throws clause.

---

## Data Files

| File | Location | Description |
|---|---|---|
| `extracted_metadata_final.json` | `PDFBOX-v5/` | Input. One entry per method with signature, body, javadoc, dependency_signatures, source_file_imports. |
| `class_inventory.json` | `PDFBOX-v5/` | All classes with constructors, factory methods, inheritance. Written by `build_class_inventory.py`. |
| `dependency_chains.json` | `test_generator/` | Per-method parameter construction chains keyed by `"full_name\|signature"`. Written by `build_dependency_chains.py`. |
| `call_graph.json` | `PDFBOX-v5/pdfbox/` | Real call sites for each method (usage examples). |
| `results/results.json` | `PDFBOX-v5/results/` | Cumulative results. Append-on-write; supports resume. |

---

## Result Schema

Each entry in `results.json` is keyed by `unique_key` (`full_name` or `full_name_overload_N`):

```json
{
  "status":          "PASSED | FAILED | COMPILE_FAILED | API_ERROR | EXTRACTION_FAILED | ALLOWLIST_FAILED | ALLOWLIST_FAILED_ON_RETRY | CHECKLIST_FAILED",
  "path":            "simple | complex | complex_resource_fallback",
  "retry_triggered": true,
  "retry_succeeded": true,
  "retry_count":     1,
  "allowlist_violations": ["ClassName.methodName"],
  "error_message":   "...",
  "test_file":       "absolute/path/to/Test.java",
  "timestamp":       "2026-04-11 14:23:01.123456"
}
```

`path` is only present on results written by `pipeline_v5.py`. Results from
`pipeline_step3.py` do not include it.

---

## Retry Budgets

| Layer | Budget | Owner |
|---|---|---|
| Checklist fix | ≤ `MAX_RETRIES` | Internal to `validate_checklist()` |
| Resource fix | ≤ `MAX_RETRIES` | Internal to `validate_resources()` |
| Allowlist fix (initial generation) | ≤ 2 (hardcoded) | Inline in `run_simple_path()` |
| Allowlist fix (Maven retry code) | ≤ 2 (hardcoded) | Inline in `run_simple_path()` |
| Maven compile/run | ≤ `config.MAX_RETRIES` | Inline in `run_simple_path()` |

Allowlist retries and Maven retries are independent budgets — exhausting one does not consume the other.

---

## Overload Handling

When a class has multiple overloads of the same method name, each gets:
- `unique_key` = `full_name_overload_0`, `full_name_overload_1`, ...
- Test class name = `ClassName_methodName_0_Test`, `ClassName_methodName_1_Test`, ...
- Separate entry in `results.json`

The rename from base class name to indexed name happens in `run_simple_path()` after generation,
before saving and before Maven.

---

## Adding a New Prompt

1. Add the builder function in `src/prompt_builder.py`.
2. Call `save_prompt(..., prompt_type='your_label')` and
   `save_response(..., response_type='your_label')` when saving — this writes
   `_your_label_prompt.txt` / `_your_label_response.txt` to the prompts/responses dirs.
3. No changes needed to `file_manager.py`.

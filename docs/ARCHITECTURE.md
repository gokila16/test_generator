# Test Case Generation Architecture

This document describes how the test-generation pipeline drives an LLM (Gemini
2.5 Flash via Vertex AI) to produce JUnit 5 unit tests for individual Java
methods of a target project (currently PDFBox). It explains the data fed to
the model, the context it sees, the checklists and validators that constrain
its output, and the loops that recover from failures.

---

## 1. High-level pipeline

The pipeline (`pipeline_step3.py`) runs in three offline stages and one
LLM-driven stage. Stages 1–3 build the static context that the LLM will
consume; Stage 4 is the actual generation loop.

```
            ┌─────────────────────────────────────────────────────────┐
            │  STAGE 1: extract method metadata                        │
            │   pipeline_step1.py / extract_for_testable_methods.py    │
            │   Output: extracted_metadata_final.json                  │
            └─────────────────────────────────────────────────────────┘
                                    │
                                    ▼
            ┌─────────────────────────────────────────────────────────┐
            │  STAGE 2: build supporting context                       │
            │   - build_class_inventory.py → class_inventory.json      │
            │   - build_dependency_chains.py → dependency_chains.json  │
            │   - build_resource_metadata.py → test_resource_metadata  │
            │   - call_graph.json (from Understand probe)              │
            └─────────────────────────────────────────────────────────┘
                                    │
                                    ▼
            ┌─────────────────────────────────────────────────────────┐
            │  STAGE 3: static behavioral analysis (per method)        │
            │   - behavioral_analyzer.py  (AST: throws/returns/ifs)    │
            │   - cfg_slicer.py           (path slices, ternaries)     │
            └─────────────────────────────────────────────────────────┘
                                    │
                                    ▼
            ┌─────────────────────────────────────────────────────────┐
            │  STAGE 4: LLM-driven generation (pipeline_step3.py)      │
            │   plan → generate → allowlist → maven → retry            │
            └─────────────────────────────────────────────────────────┘
```

Only Stage 4 calls the LLM. Everything fed to the model has already been
extracted, normalized, and verified by the earlier stages.

---

## 2. The inputs the LLM sees

The LLM never reads the codebase directly. For every method under test, the
pipeline assembles a single self-contained prompt from these sources:

| Source file                     | What it contributes                                               |
| ------------------------------- | ----------------------------------------------------------------- |
| `extracted_metadata_final.json` | Method signature, body, javadoc, source-file imports, dep sigs.   |
| `class_inventory.json`          | Per-class constructors, factory methods, public methods, subclasses. |
| `dependency_chains.json`        | Concrete code snippets to construct each parameter and the receiver. |
| `call_graph.json`               | Real call sites of the method elsewhere in the codebase.          |
| `test_resource_metadata.json`   | Real test files on disk (with encryption info / passwords).       |
| Static analyzers (AST + slicer) | Branch map, throw contracts, literal return values, ternary paths.|

### 2.1 Per-method metadata (the “method object”)

A method dict carries these fields into `build_planning_prompt`:

- `class_name`, `method_name`, `full_name` — identity used for filenames and
  test class naming.
- `signature` — full Java signature, including modifiers and `throws` clause.
- `body` — raw Java source of the method, copied verbatim into the prompt.
- `javadoc` — original Javadoc if present.
- `source_file_imports` — every `import …;` line from the file that declares
  the method. The LLM is told these are the **only** imports it may add for
  project classes.
- `dependency_signatures` — the methods on other project classes that the SUT
  *actually calls*. This is the allowlist used both inside the prompt and by
  the post-generation static validator.

### 2.2 Per-method derived context

For each method, the pipeline also resolves:

- **Dependency chain** (`get_dependency_chain`) — verbatim construction code
  for the receiver and every parameter, produced ahead of time.
- **Caller snippets** (`get_caller_snippets`, capped at 2) — real call sites
  in the codebase, shown as “REAL USAGE EXAMPLES”.
- **Class inventory entries** for every class in `source_file_imports` —
  whether it’s abstract / interface / final, what its public constructors and
  factory methods are, up to 25 public methods, and known concrete subclasses.
- **Pre-computed behavioral facts** — extracted on the fly by
  `behavioral_analyzer.py`:
  - Branch map: `if (cond) -> taken outcome | else -> not_taken outcome`
  - Throw contracts: `throws X when (cond)`
  - Literal return values per path
  - Ternary return paths from `cfg_slicer.py`

These are presented to the LLM as **ground truth** so it does not re-derive
expected values from the implementation (the main source of oracle
hallucination).

---

## 3. The prompts (what the LLM is fed)

Test generation is a **two-step LLM workflow**, with up to three additional
LLM-driven recovery paths. All prompts are built in `src/prompt_builder.py`.

### 3.1 Step 1 — Planning prompt (`build_planning_prompt`)

The model is given a senior-SDET persona and asked to output a **structured
plan only, no Java code**. The prompt is assembled in this order so that
grounding data appears before instructions:

```
1.  Persona + "how you think" (path-based reasoning, not logic-copying)
2.  Bad-test examples to avoid (assertNotNull alone, mirroring the method)
3.  METHOD UNDER TEST  — signature, javadoc, body
4.  REAL USAGE EXAMPLES FROM CODEBASE        (from call_graph.json)
5.  HOW TO CONSTRUCT EACH INPUT              (from dependency_chains.json)
6.  RECEIVER CONSTRUCTION NOTE               (if receiver is abstract)
7.  DEPENDENCY SIGNATURES                    (callable methods on dep classes)
8.  AVAILABLE PROJECT CLASSES                (from class_inventory.json)
9.  AVAILABLE TEST RESOURCE FILES            (if method is file-dependent)
10. SOURCE FILE IMPORTS                      (the only legal project imports)
11. PRE-COMPUTED BEHAVIORAL FACTS            (branch map / throws / returns)
12. THE TASK: three questions + required output format
```

The “three questions” are the checklist the LLM must answer before writing
the plan:

- **Q1 — What does this method do?** Single-sentence intent statement.
- **Q2 — Path-by-path test table.** For every if/else branch, guard clause,
  catch block, early return, and loop-skip: state the input that forces that
  path and the *exact* expected outcome (literal value, exception type, or
  observable getter result). Anchored to the pre-computed facts.
- **Q3 — Regression scenarios.** For each parameter, one high-leverage input
  (null, empty, negative, MAX_VALUE, partially-initialised) and the contract
  for what should happen.

The required output format is rigid: METHOD SEMANTICS → PATH ANALYSIS →
PLANNED IMPORTS → TEST METHODS (one per path, with name, path covered,
regression target, setup, input, method call, expected output, assertion)
→ PATH COVERAGE SUMMARY.

### 3.2 Step 2 — Generation-from-plan prompt (`build_generation_from_plan_prompt`)

Given the accepted plan, the LLM is now asked to translate it into a single
JUnit 5 Java file. The prompt explicitly forbids re-planning:

> "Your job is to translate each test into compilable code — not to re-derive
> or second-guess the plan."

It is also handed `HOW TO CONSTRUCT EACH INPUT` and `AVAILABLE TEST RESOURCE
FILES` again, so it does not have to extract them from the plan text.

The hard rules baked into this prompt (the “Quality Gate”):

1. Class name = `<SUT>_<method>_Test`, package = SUT package.
2. Implement exactly the test methods named in the plan.
3. Only add imports listed in `PLANNED IMPORTS`. Remove any unreferenced.
4. Use the exact construction code from the plan / HOW TO CONSTRUCT verbatim.
5. No method chaining — assign return values to named variables first.
6. Never pass bare `null` to overloaded methods — always cast `(Type) null`.
7. No Mockito, no anonymous subclasses (`new X() { … }` is forbidden).
8. Every assertion must use a hardcoded expected value from the plan —
   `assertNotNull` alone, `assertTrue(true)`, or an empty body are rejected.
9. The SUT class must appear in the body, not just an import.
10. Helper types may exist only as nested static classes.
11. Declare checked exceptions on the test method’s `throws`.

The comment rules are restrictive: only four cases warrant a comment
(non-obvious setup, non-obvious expected value, intentionally surprising
input, or a workaround). Restating-the-code comments are forbidden.

The prompt ends with the literal ```` ```java\npackage ```` so the model
can only emit raw Java from the package declaration onward.

### 3.3 Recovery / retry prompts

Three recovery prompts handle different failure modes. None of them re-plans
from scratch — they all anchor on the previously accepted artifact:

| Prompt builder                       | Triggered by                                       | Anchored on        |
| ------------------------------------ | -------------------------------------------------- | ------------------ |
| `build_recovery_prompt`              | Extraction or static rejection of the generation   | the accepted plan  |
| `build_allowlist_violation_prompt`   | Hallucinated method / wrong arity / bad import     | the violating code |
| `build_retry_prompt`                 | Maven compile or test runtime failure              | the failing code   |

`build_recovery_prompt` reads from a `_RECOVERY_INSTRUCTIONS` table keyed by
fail reason (`no_code_block`, `no_test_annotation`, `sut_missing`,
`trivial_assertions`, `no_test_methods`) and produces a targeted explanation
of what was wrong, plus the accepted plan and the rejected raw response.

`build_allowlist_violation_prompt` separates violations into three buckets —
HALLUCINATED METHODS, WRONG ARGUMENT COUNT, and HALLUCINATED IMPORTS — and
chooses a header sentence that reflects which mix is present. For type
mismatches it lists every known overload’s parameter type list so the model
can pick a real one.

`build_retry_prompt` classifies the Maven error message:

- For type/constructor errors (`incompatible types`, `no suitable constructor`,
  `cannot find symbol: constructor`, `cannot be applied to`) it allows the
  model to rewrite the affected variable’s declaration + construction block,
  but nothing else.
- For any other error it requires a minimal fix to the single failing line.

It also embeds an `ERROR DIAGNOSIS GUIDE` mapping common compile errors to
their likely causes (ambiguous null reference → cast it; cannot find symbol:
method → check allowed dep sigs; cannot be instantiated → class is abstract).

---

## 4. The checklists the LLM must satisfy

The LLM’s output passes through a layered set of validators. Anything that
fails kicks off a recovery prompt with its own retry budget. Layers, in order:

### 4.1 Extraction (`src/code_extractor.py`)

- Response must contain `@Test`.
- A ```` ```java ```` block, a plain ``` ``` block, or a `package … ;` /
  `import … ;` / `public class …` opening is required.
- Failure reasons fed back to recovery: `no_code_block`, `no_test_annotation`.

### 4.2 Static post-processing (`src/java_post_processor.py`)

A tiered set of structural checks, sorted by how Maven would react:

- **Tier 1** — no usable Java (`no_code_block`, `no_test_annotation`). Trigger
  recovery prompt with the plan.
- **Tier 2** — Java exists but Maven won’t surface the problem (`sut_missing`,
  `trivial_assertions`, `no_test_methods`). Trigger recovery prompt with the
  plan.
- **Tier 3** — Java exists and Maven *will* produce a compile error
  (`truncated`, `mockito_import`, `class_redefinition`,
  `nested_class_redefinition`, `constructor_arity:…`). Skip recovery, apply
  in-place fixes via `apply_java_fixes`, hand straight to Maven.

`_check_class_redefinition` rejects extra top-level types. `_is_sut_missing`
flags responses that import the SUT but never use it in the body.
`_check_constructor_arities` cross-references every `new X(…)` against
`class_inventory.json` to catch invented constructors before compilation.

### 4.3 Import validation (`validate_imports` in `src/allowlist_checker.py`)

An import is allowed iff one of:

- starts with `java.` / `javax.`
- starts with `org.junit.`
- appears verbatim in `source_file_imports`
- exists as a key in `class_inventory.json`
- shares a package prefix (depth 2 or 3) with anything in the inventory

Anything else is emitted as `HALLUCINATED_IMPORT::fqn`.

### 4.4 Method-call allowlist (`check_against_allowlist`)

This is the biggest hallucination guard. The checker:

1. Builds an **allowlist** of `(ClassName, methodName) → [overload arities]`
   from `dependency_signatures` plus the SUT method itself.
2. Parses every variable declaration in the generated code to build a
   `type_map: varName → ClassName`.
3. Enriches `type_map` with chained assignments by reading return types from
   the dependency signatures (`_enrich_type_map`).
4. Walks every `receiver.methodName(` call in the source:
   - Skips JUnit assertion classes and universal `Object` methods (`equals`,
     `hashCode`, `toString`, `getClass`, `wait`, `notify`, `notifyAll`).
   - Resolves the receiver to a class via `type_map` (lowercase) or treats it
     as a static call (uppercase).
   - For dep classes: checks the method exists *and* the call’s arity matches
     a known overload. Wrong arity → `TYPE_MISMATCH::Class.method::n_args::overloads`.
   - For inventory classes: validates against `public_methods` from the
     inventory.
   - Unresolved receivers are skipped (no false-positives).

Each violation feeds `build_allowlist_violation_prompt` directly. The
allowlist loop runs up to `MAX_ALLOWLIST_RETRIES = 2` before giving up.

### 4.5 Maven execution (`src/maven_runner.py`)

The final gate: actually compile and run the generated test against the real
project. Compile errors and runtime test failures both feed
`build_retry_prompt`, which runs up to `MAX_RETRIES = 2` retries.

### 4.6 Pipeline status outcomes

Every method ends in one of these terminal states (written to
`results/results.json`):

`PASSED`, `FAILED`, `COMPILE_FAILED`, `ALLOWLIST_FAILED`,
`ALLOWLIST_FAILED_ON_RETRY`, `EXTRACTION_FAILED`, `API_ERROR`.

---

## 5. End-to-end timeline of a single method

A successful run for one method involves at minimum two LLM calls, and at
most ~8 if every layer triggers a retry:

```
  plan_prompt           ──► LLM  ──►  plan
  gen_prompt(plan)      ──► LLM  ──►  raw response
                                          │
                                          ▼
                          extract_java_code  ─── fail ──► (recovery prompt) ──► LLM
                                          │
                                          ▼
                          post_process_java  ─── tier 1/2 fail ──► (recovery) ──► LLM
                                          │                  tier 3 fail ──► apply_java_fixes
                                          ▼
                          validate_imports   ─── fail ──► (allowlist violation prompt) ──► LLM
                                          │                 (loop ≤ 2 retries)
                                          ▼
                          check_against_allowlist  ─── fail ──► (allowlist prompt) ──► LLM
                                          │                       (loop ≤ 2 retries)
                                          ▼
                          compile_and_run    ─── compile or test fail ──► (retry prompt) ──► LLM
                                          │                                (loop ≤ 2 retries)
                                          ▼
                          PASSED / FAILED / COMPILE_FAILED / ALLOWLIST_FAILED / …
```

Each round-trip is persisted: prompts go under `prompts/`, plans under
`plans/`, raw responses under `responses/`, final Java under
`generated_tests/`, and the per-method outcome under `results/results.json`.
This makes every decision (and every retry the LLM was asked for)
reproducible from disk without re-querying Gemini.

---

## 6. LLM settings

From `config.py`:

- Model: `gemini-2.5-flash` (via Vertex AI, `us-central1`).
- `max_output_tokens = 16384` — large, because the plan + generation can run
  long for complex methods.
- `temperature = 0` — deterministic generations; the same prompt produces the
  same output, which keeps failures reproducible.
- `API_SLEEP_SEC = 1` between calls; on `429`/`RESOURCE_EXHAUSTED`, the
  client backs off 60s and retries once.
- Truncation handling: if Gemini returns `finish_reason = MAX_TOKENS`,
  `call_llm` issues a single in-conversation continuation turn
  (user → model → user-with-`_CONTINUATION_PROMPT`) and concatenates the two
  responses before returning.

---

## 7. Summary of why each piece exists

| Component                              | Failure mode it defends against                           |
| -------------------------------------- | --------------------------------------------------------- |
| Plan-then-generate split               | LLM writing code before reasoning about paths.            |
| Pre-computed behavioral facts          | Oracle hallucination (re-deriving expected values).       |
| HOW TO CONSTRUCT EACH INPUT            | Hallucinated constructors / wrong parameter shapes.       |
| AVAILABLE PROJECT CLASSES (inventory)  | LLM removing tests because it can’t find how to build X.  |
| AVAILABLE TEST RESOURCE FILES          | Tests using `File.createTempFile()` for happy-path inputs.|
| Source-file imports as allowlist       | Hallucinated import statements / package guesses.         |
| Allowlist + type-map call check        | Hallucinated method names / wrong-arity overload calls.   |
| Constructor-arity check (inventory)    | Hallucinated constructors that compile-fail at Maven.     |
| Tiered post-processing (Tier 1/2/3)    | Avoiding a recovery round-trip when Maven would say more. |
| Targeted recovery prompts              | Recovering without re-planning from scratch.              |
| Error-classified retry prompt          | Forcing minimal fixes vs. structured rewrites correctly.  |
| Plan-anchored recovery                 | Preventing scope creep between retries.                   |
| Persisted prompts/plans/responses      | Reproducibility, manual triage, regression analysis.      |

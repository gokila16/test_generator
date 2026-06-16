# V1 retro-grading: apples-to-apples vs V7

V7's pipeline rejects an LLM-generated test before compilation if it fails any
of these extraction-stage gates (implemented in
`test_generator_v7/test_generator/src/java_post_processor.py::post_process_java`):

- `no_test_methods` — no `@Test` annotated methods
- `trivial_assertions` — every `@Test` method is empty / `assertTrue(true)` /
  `assertNotNull(...)`-only / has no assertion at all
- `sut_missing` — the class under test never appears in the file body
- `class_redefinition` — top-level class/interface/enum redefinitions (catches
  shell tests that paste a fake `PDDocument`, etc., instead of using the real
  class). Effectively also catches the "anonymous subclass / stub" pattern
  the professor described, because such files almost always include a paste
  of the parent class or supporting types.
- `nested_class_redefinition` — nested types shadow production classes (only
  fires with a class inventory; not used in the retro pass)
- `mockito_import` — forbidden Mockito dependency
- `truncated` — file doesn't end with `}`
- `constructor_arity:*` — call with no matching constructor (inventory-only;
  not used in the retro pass)

V1's pipeline does not enforce any of these post-hoc. Any test that compiles
and runs is counted as PASSED, including ones V7 would have rejected outright.
`retro_grade.py` applies V7's exact `post_process_java` to every V1 PASSED
`.java` file on disk and reclassifies the rejected ones into a new
`EXTRACTION_FAILED_RETRO` bucket.

## Side-by-side pass rates

| Model | V1 (original) | V1 (V7-graded) | Δ | Reclassified |
|---|---:|---:|---:|---:|
| gpt-5-mini | **70.6%** (924/1309) | **63.6%** (833/1309) | -7.0 pp | 91 |
| Gemini 3 Flash | **73.9%** (967/1309) | **68.2%** (893/1309) | -5.7 pp | 74 |
| DeepSeek | **60.4%** (791/1309) | **57.8%** (756/1309) | -2.7 pp | 35 |

Total methods per run: 1309 (shared metadata across all three pipelines).

## Reclassification breakdown by reason

| Reason | gpt-5-mini | Gemini | DeepSeek |
|---|---:|---:|---:|
| `class_redefinition` | 55 | 0 | 0 |
| `trivial_assertions` | 33 | 14 | 31 |
| `mockito_import` | 2 | 49 | 1 |
| `sut_missing` | 1 | 11 | 3 |
| **Total** | **91** | **74** | **35** |

Two clear per-model signatures:

- **gpt-5-mini** leans on `class_redefinition` — it fabricates stub copies of
  production classes (e.g., redefining `COSArray`, `PDDocument`, etc., at the
  top of the test file) so that its tests can be self-contained. Those
  "tests" pass Maven but exercise the fake class, not the real one.
- **Gemini** leans on `mockito_import` — it ignores V1's no-mocks instruction
  49 times. (V7 strips these at extraction time.)
- **DeepSeek** is dominated by `trivial_assertions` — many `@Test` methods
  with no real assertions, or only `assertNotNull(...)` placeholders.

## Why DeepSeek's drop is smaller than predicted

The professor's directional estimate was Gemini → high 60s and
DeepSeek → low 50s. Gemini lands at 68.2% (consistent). DeepSeek lands at
57.8% (smaller drop than expected). The reason is the upstream funnel:
DeepSeek already loses 347/1309 tests at COMPILE_FAILED before they reach the
PASSED bucket — its PASSED pool is the smallest of the three, so there is
less material for V7's gates to reject. Gemini and gpt-5-mini compile
much more code, so they have proportionally more PASSED tests that fail the
quality bar.

## Caveats

1. `nested_class_redefinition` and `constructor_arity:*` are silently skipped
   in the retro pass because they need a class inventory keyed to PDFBox
   internals. Adding the inventory would only ever *strengthen* the retro
   gate (move more tests into the failed bucket), so current retro pass rates
   are a conservative upper bound — V7's true equivalent rubric would push
   V1's numbers down a bit further, not up.
2. The retro pass operates on the saved `.java` file post-Maven, so any
   rewrites V1 made before saving are baked in. V7's extraction stage runs on
   the raw LLM response, but in practice the only V1-side rewrite is package
   injection, which `post_process_java` also applies, so the comparison is
   faithful.
3. V7's "anonymous-subclass" rejection is not a dedicated check — it is
   implicitly caught by `class_redefinition` and `sut_missing` when the test
   stubs the SUT instead of using it. None of the 200 reclassified entries
   across the three models are pure anonymous-subclass cases.

## Reproducing

```bash
cd c:/Users/Harini/Documents/thesis_research/testgenerator_v1/test_generator

# gpt-5-mini (current dir's config.py)
python retro_grade.py

# Gemini
python retro_grade.py --config-path \
  c:/Users/Harini/Documents/thesis_research/test_generator_v1_gemini/test_generator/config.py

# DeepSeek
python retro_grade.py --config-path \
  c:/Users/Harini/Documents/thesis_research/test_generation_deepseek_v1/test_generator/config.py
```

Each run writes `results_retro.json` next to the variant's original
`results.json`. The original `results.json` is never mutated.

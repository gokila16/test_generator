# Pass rate by resolution-quality bucket

`dependency_chains.json` tags each of the 1309 methods with one of four
resolution qualities — confirming the prof's estimate exactly:

| Quality | Count | Share |
|---|---:|---:|
| full | 1048 | 80.1% |
| partial | 113 | 8.6% |
| unresolvable | 108 | 8.3% |
| unknown | 40 | 3.1% |

## V7 pass rate per bucket

| Bucket | gpt-5-mini V7 | Gemini V7 | DeepSeek V7 |
|---|---:|---:|---:|
| **full** (n=1048) | **77.1%** | **88.5%** | **67.1%** |
| **partial** (n=113) | **67.3%** | **49.6%** | **52.2%** |
| **unresolvable** (n=108) | **66.7%** | **69.4%** | **36.1%** |
| **unknown** (n=40) | **75.0%** | **82.5%** | **57.5%** |
| Decay full → partial → unresolvable | clean | broken | clean |
| Decay including unknown | violated | violated | violated |

## V1 pass rate per bucket (no construction chains)

| Bucket | gpt-5-mini V1 | Gemini V1 | DeepSeek V1 |
|---|---:|---:|---:|
| full (n=1048) | 72.9% | 76.5% | 62.5% |
| partial (n=113) | 65.5% | 68.1% | 60.2% |
| unresolvable (n=108) | 54.6% | 55.6% | 39.8% |
| unknown (n=40) | 67.5% | 70.0% | 62.5% |

## Interpretation

**Strict monotonic decay does not hold for any model.** "Unknown" rebounds
above unresolvable in every run. But that's because the "unknown" bucket
is contaminated: spot-checking shows it contains zero-arg anonymous-inner
class methods (e.g., `COSStream.createOutputStream.(Anon_1).close`) and
`Collection`-typed parameters where the resolver opts out. These are
typically easier to test than truly unresolvable methods, not harder. The
bucket label is misleading for ranking purposes.

**The clean signal is full → partial → unresolvable.** Restricting to those
three buckets:

- gpt-5-mini V7: 77.1% > 67.3% > 66.7% — clean decay
- Gemini V7:     88.5% > 49.6% < 69.4% — *broken at partial*
- DeepSeek V7:   67.1% > 52.2% > 36.1% — clean decay

The full → partial drop is large and consistent across all three models
(-9.8, -39.0, -14.9 pp). That part of the claim "chains are doing real
work" holds up.

**But the V1 baseline also decays.** This is the more important caveat:
V1 doesn't use construction chains at all, yet pass rate still drops from
full to unresolvable across all three models (gpt-5-mini -18.3 pp, Gemini
-20.9 pp, DeepSeek -22.7 pp). So part of the bucket-level decay is just
that "harder methods are harder for any pipeline" — the resolver gives up
on complex APIs, and complex APIs are harder to test even without
construction chains.

**V7-minus-V1 delta per bucket** is the disentangled measure:

| Bucket | gpt-5-mini Δ | Gemini Δ | DeepSeek Δ |
|---|---:|---:|---:|
| full | +4.2 pp | +12.0 pp | +4.6 pp |
| partial | +1.8 pp | **-18.5 pp** | **-8.0 pp** |
| unresolvable | +12.1 pp | +13.8 pp | -3.7 pp |
| unknown | +7.5 pp | +12.5 pp | -5.0 pp |

Two observations from the delta table:

1. **V7 reliably wins on full chains** (+4 to +12 pp). When the resolver
   produced a complete chain, V7 always beats V1.
2. **V7 hurts on partial chains for Gemini and DeepSeek** (-18 and -8 pp).
   A partial chain seems to actively mislead the LLM — better to give it
   nothing than half the answer. Worth investigating: do partial chains
   contain wrong constructor calls that the LLM trusts?

## Bottom line for the paper

- The clean story is: **V7's pass rate decays from full to partial to
  unresolvable for two of three models** (gpt-5-mini, DeepSeek). Strict
  4-bucket decay including "unknown" does not hold and probably should
  not be claimed.
- The stronger evidence that chains do real work is the **V7 vs V1 delta
  on the "full" bucket**: +4 to +12 pp in every model. Methods with
  complete chains are exactly where V7 should win, and it does.
- The **"partial chains may be harmful"** finding is unexpected and worth
  a separate paragraph — Gemini and DeepSeek both perform worse with
  partial chains than V1's no-chain baseline. That's a real reviewer-
  worthy nuance, not a bug to bury.

## Reproducing

```bash
cd c:/Users/Harini/Documents/thesis_research/testgenerator_v1/test_generator

# V7 - the three models the prof asked about
python resolution_breakdown.py --label "gpt-5-mini V7"
python resolution_breakdown.py --label "Gemini V7" \
  --results c:/Users/Harini/Documents/thesis_research/PDFBOX-v5/generated_files/gemini3_v7/results/results.json
python resolution_breakdown.py --label "DeepSeek V7" \
  --results c:/Users/Harini/Documents/thesis_research/PDFBOX-v5/generated_files/deepseek3_v7/results/results.json

# V1 - same chains file, V1 results paths, for the disentangling check
python resolution_breakdown.py --label "gpt-5-mini V1" \
  --results c:/Users/Harini/Documents/thesis_research/PDFBOX-v5/generated_files/gpt5mini-v1/results/results.json
python resolution_breakdown.py --label "Gemini V1" \
  --results c:/Users/Harini/Documents/thesis_research/PDFBOX-v5/generated_files/gemini3flash-v1/results/results.json
python resolution_breakdown.py --label "DeepSeek V1" \
  --results c:/Users/Harini/Documents/thesis_research/PDFBOX-v5/generated_files/deepseek_v1/results/results.json
```

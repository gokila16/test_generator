from datetime import datetime

def print_progress(processed, total, results):
    """Prints progress every 10 methods"""
    counts = {}
    for r in results.values():
        s = r.get('status', 'UNKNOWN')
        counts[s] = counts.get(s, 0) + 1

    print(f"\n--- Progress: {processed}/{total} ---")
    for status, count in counts.items():
        print(f"  {status}: {count}")

def print_final_report(results, report_path, start_time):
    """Prints and saves final report"""
    total = len(results)
    counts = {}
    for r in results.values():
        s = r.get('status', 'UNKNOWN')
        counts[s] = counts.get(s, 0) + 1

    retried = sum(
        1 for r in results.values()
        if r.get('retry_triggered')
    )
    retry_success = sum(
        1 for r in results.values()
        if r.get('retry_succeeded')
    )

    elapsed = (datetime.now() - start_time).seconds // 60

    lines = [
        "=" * 40,
        "FINAL RESULTS",
        "=" * 40,
        f"Total methods:     {total}",
    ]
    for status, count in sorted(counts.items()):
        pct = count / total * 100 if total > 0 else 0
        lines.append(f"{status:<20} {count} ({pct:.1f}%)")

    lines += [
        f"Retry triggered:   {retried}",
        f"Retry succeeded:   {retry_success}",
        f"Total time:        {elapsed} minutes",
    ]

    # Path breakdown (only present when pipeline_v5 has run)
    path_counts = {}
    for r in results.values():
        p = r.get('path')
        if p:
            path_counts[p] = path_counts.get(p, 0) + 1

    if path_counts:
        lines.append("")
        lines.append("--- Path breakdown ---")
        for path, count in sorted(path_counts.items()):
            pct = count / total * 100 if total > 0 else 0
            lines.append(f"  {path:<30} {count} ({pct:.1f}%)")

        # How often the resource fallback actually passed
        fallback_total = path_counts.get('complex_resource_fallback', 0)
        fallback_passed = sum(
            1 for r in results.values()
            if r.get('path') == 'complex_resource_fallback'
            and r.get('status') == 'PASSED'
        )
        if fallback_total:
            lines.append(
                f"  resource_fallback passed: {fallback_passed}/{fallback_total}"
            )

    lines.append("=" * 40)

    report = '\n'.join(lines)
    print(report)

    with open(report_path, 'w') as f:
        f.write(report)
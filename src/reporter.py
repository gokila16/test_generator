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

def print_final_report(results, report_path, start_time, truncation_count=0):
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

    fail_reasons = {}
    for r in results.values():
        if r.get('status') == 'EXTRACTION_FAILED':
            reason = r.get('extraction_fail_reason') or 'unknown'
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

    lines += [
        f"Retry triggered:   {retried}",
        f"Retry succeeded:   {retry_success}",
        f"Truncations cont.: {truncation_count}",
        f"Total time:        {elapsed} minutes",
    ]

    if fail_reasons:
        lines.append("--- Extraction fail reasons ---")
        for reason, count in sorted(fail_reasons.items()):
            lines.append(f"  {reason:<25} {count}")

    lines.append("=" * 40)

    report = '\n'.join(lines)
    print(report)

    with open(report_path, 'w') as f:
        f.write(report)
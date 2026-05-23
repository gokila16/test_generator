# rebuild_understand_db.ps1
#
# Rebuilds the SciTools Understand database for PDFBox so it spans every
# module the test-generation pipeline depends on -- not just `pdfbox`, but
# also `pdfbox-io` and `pdfbox-rendering`. This is the Tier 1 fix for the
# "Unknown Class" gap surfaced by probe_understand_refs.py: classes like
# RandomAccessReadBufferedFile and IOUtils are referenced by methods in
# `pdfbox`, but their definitions live in `pdfbox-io` and were never analyzed
# into the original main.und, so Understand could not report their
# constructors/methods.
#
# The script writes a NEW database at:
#   PDFBOX-v5\pdfbox\src\main\main_full.und
#
# The original main.und is left untouched, so the existing extractor and the
# probe keep working unchanged. Once you've verified the new DB with the
# probe, point both scripts at main_full.und.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\rebuild_understand_db.ps1
#   powershell -ExecutionPolicy Bypass -File .\rebuild_understand_db.ps1 -Force
#
# -Force skips the "delete existing DB?" prompt and overwrites without asking.

param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# --- Configuration --------------------------------------------------------

$UND    = 'C:\Program Files\SciTools\bin\pc-win64\und.exe'
$BASE   = 'C:\Users\Harini\Documents\thesis_research\PDFBOX-v5'
$DB_OUT = Join-Path $BASE 'pdfbox\src\main\main_full.und'

# The four core modules pdfbox itself can depend on. preflight/debugger/
# examples/tools/benchmark are downstream consumers of pdfbox, not its
# dependencies, so they are intentionally excluded from the analysis.
$SOURCE_DIRS = @(
    (Join-Path $BASE 'pdfbox\src\main\java'),
    (Join-Path $BASE 'io\src\main\java'),
    (Join-Path $BASE 'fontbox\src\main\java'),
    (Join-Path $BASE 'xmpbox\src\main\java')
)

# --- Validation -----------------------------------------------------------

if (-not (Test-Path $UND)) {
    Write-Host "ERROR: und.exe not found at:" -ForegroundColor Red
    Write-Host "  $UND"
    Write-Host ""
    Write-Host "Find the right path with:"
    Write-Host "  Get-ChildItem 'C:\Program Files\SciTools' -Recurse -Filter und.exe"
    Write-Host "Then edit `$UND at the top of this script."
    exit 1
}

$missing = @()
foreach ($d in $SOURCE_DIRS) {
    if (-not (Test-Path $d)) { $missing += $d }
}
if ($missing.Count -gt 0) {
    Write-Host "ERROR: the following source directories do not exist:" -ForegroundColor Red
    foreach ($d in $missing) { Write-Host "  $d" }
    Write-Host ""
    Write-Host "Confirm your PDFBox checkout layout, then edit `$BASE / `$SOURCE_DIRS at the top of this script."
    exit 1
}

# --- Handle existing output DB --------------------------------------------

if (Test-Path $DB_OUT) {
    Write-Host "Existing DB found at:" -ForegroundColor Yellow
    Write-Host "  $DB_OUT"
    if (-not $Force) {
        $resp = Read-Host "Delete and rebuild? (y/N)"
        if ($resp -ne 'y' -and $resp -ne 'Y') {
            Write-Host "Aborted. To use a different output path, edit `$DB_OUT at the top of this script."
            exit 0
        }
    }
    Remove-Item -Recurse -Force $DB_OUT
    $sidecar = "$DB_OUT.lock"
    if (Test-Path $sidecar) { Remove-Item -Force $sidecar }
}

# --- Build ----------------------------------------------------------------

Write-Host ""
Write-Host "[1/3] Creating DB ..." -ForegroundColor Cyan
Write-Host "      $DB_OUT"
& $UND create -languages Java -db $DB_OUT
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: und create failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[2/3] Adding source directories ..." -ForegroundColor Cyan
foreach ($d in $SOURCE_DIRS) {
    Write-Host "      + $d"
    & $UND add $d -db $DB_OUT
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: und add failed for $d (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[3/3] Analyzing (this can take a few minutes) ..." -ForegroundColor Cyan
& $UND analyze -db $DB_OUT
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: und analyze failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# --- Done -----------------------------------------------------------------

Write-Host ""
Write-Host "Done. New DB at:" -ForegroundColor Green
Write-Host "  $DB_OUT"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Update probe_understand_refs.py:"
Write-Host "       DB_PATH = BASE / 'PDFBOX-v5' / 'pdfbox' / 'src' / 'main' / 'main_full.und'"
Write-Host "     ...and re-run the probe. The classes-of-interest sanity check should now"
Write-Host "     report YES for RandomAccessReadBufferedFile, IOUtils, and RandomAccessRead."
Write-Host ""
Write-Host "  2. Once the probe looks clean, update enrich_extracted_metadata_testable.py to"
Write-Host "     point at main_full.und as well, then we will apply the broadened ref-query patch."

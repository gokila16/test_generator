from pathlib import Path

UND_DB   = Path("/Users/apple/Documents/research/thesis_research/v5/pdfbox/src/main/main.und")
SRC_ROOT = Path("/Users/apple/Documents/research/thesis_research/v5/pdfbox/src/main/java")
OUT_DIR  = Path("/Users/apple/Documents/research/thesis_research/test_generator")

PUBLIC_KINDS: frozenset[str] = frozenset({
    "Public Method",
    "Public Static Method",
    "Public Final Method",
    "Public Static Final Method",
    "Public Generic Method",
    "Public Static Generic Method",
})

MAX_SNIPPETS = 3

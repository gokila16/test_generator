from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class ParsedName:
    """Components of an Understand fully-qualified method name."""
    package: str
    outer_class: str
    inner_classes: list[str]
    method_name: str

    @property
    def immediate_class(self) -> str:
        return self.inner_classes[-1] if self.inner_classes else self.outer_class

    @property
    def inner_class(self) -> str | None:
        return self.inner_classes[-1] if self.inner_classes else None


@dataclass
class UsageSnippet:
    file: str
    snippet: str


@dataclass
class MethodEntry:
    full_name: str
    class_name: str
    inner_class: str | None
    method_name: str
    file_path: str
    signature: str
    javadoc: str | None
    body: str
    usage_snippets: list[UsageSnippet]
    kind: str
    status: str  # "OK" | "FILE_NOT_FOUND" | "BODY_NOT_FOUND"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["usage_snippets"] = [asdict(s) for s in self.usage_snippets]
        return d

    def to_summary_row(self) -> dict:
        return {
            "full_name":     self.full_name,
            "class_name":    self.class_name,
            "method_name":   self.method_name,
            "kind":          self.kind,
            "has_body":      bool(self.body),
            "has_javadoc":   bool(self.javadoc),
            "has_snippets":  bool(self.usage_snippets),
            "snippet_count": len(self.usage_snippets),
            "status":        self.status,
        }


@dataclass
class ExtractionStats:
    total: int = 0
    ok: int = 0
    file_not_found: int = 0
    body_not_found: int = 0
    has_javadoc: int = 0
    has_snippets: int = 0

    def record(self, entry: MethodEntry) -> None:
        self.total += 1
        match entry.status:
            case "OK":            self.ok += 1
            case "FILE_NOT_FOUND": self.file_not_found += 1
            case "BODY_NOT_FOUND": self.body_not_found += 1
        if entry.javadoc:        self.has_javadoc += 1
        if entry.usage_snippets: self.has_snippets += 1

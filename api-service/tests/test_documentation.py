"""Keep moved documentation links usable in the handover package."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def test_local_documentation_links_resolve():
    root = Path(__file__).resolve().parents[1]
    documents = [
        root / "README.md",
        *root.joinpath("docs").rglob("*.md"),
        root / "deploy/README.md",
    ]
    for document in documents:
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if urlsplit(target).scheme or target.startswith("#"):
                continue
            path = unquote(target.split("#", 1)[0])
            resolved = (document.parent / path).resolve()
            # Dated full-stack audit evidence references optional UI source, excluded from API artifacts.
            if (
                not (root / "frontend").exists()
                and document.is_relative_to(root / "docs/reviews/pre-production-2026-10-22")
                and resolved.is_relative_to(root / "frontend")
            ):
                continue
            assert (document.parent / path).exists(), f"Broken link in {document}: {target}"

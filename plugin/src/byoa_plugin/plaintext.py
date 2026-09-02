"""Markdown → plain-text conversion for plain-text-only G2 displays.

Both display surfaces (Even Add-Agent chat view and the glasses-app WS
text container) render raw text; markdown markers show up literally as
``**value**`` etc. on the 576x288 mono display. This module strips the
markdown constructs the agent actually emits while leaving content
untouched.

Deliberately conservative — CommonMark-lite, not a full parser:

- fenced code blocks: fences dropped, body kept verbatim
- inline code: backticks dropped, content kept
- emphasis: ``**bold**``, ``__bold__``, ``*italic*``, ``_italic_``,
  ``~~strike~~`` markers dropped
- links: ``[text](url)`` → ``text (url)`` (url omitted when identical)
- ATX headers: leading ``#``s (+ trailing ``###`` sequence) dropped
- blockquotes: leading ``> `` markers dropped
- backslash escapes: backslash dropped, escaped char kept

Left alone (read fine as raw text): bullets, table pipes, horizontal
rules, wildcard globs, ``2*3*4``-style intra-word asterisks.

All functions are pure; idempotency is a hard requirement
(``strip_markdown(strip_markdown(x)) == strip_markdown(x)``).
"""

from __future__ import annotations

import re

# Fenced code blocks: opening fence (``` or ~~~, ≥3 chars, optional info
# string), body, closing fence. DOTALL so body spans lines. Body kept.
_FENCE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[ \t]*[^\n]*\n"  # opening fence + info string
    r"(.*?)"
    r"^[ \t]*\1[ \t]*\n?",  # closing fence (same marker, exact run)
    re.DOTALL | re.MULTILINE,
)

# Emphasis markers. Left-flanking/right-flanking heuristics (CommonMark-ish):
# a run of * or _ opens emphasis only when not followed by whitespace, and
# closes only when not preceded by whitespace. Intra-word digits and lone
# markers (globs like *.py) therefore never match.
_BOLD_RE = re.compile(r"(?<!\*)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)", re.DOTALL)
_BOLD_UNDER_RE = re.compile(r"(?<!_)__(?=\S)(.+?)(?<=\S)__(?!_)", re.DOTALL)
_STRIKE_RE = re.compile(r"(?<!~)~~(?=\S)(.+?)(?<=\S)~~(?!~)", re.DOTALL)
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*]*[^\s*])\*(?![\w*])", re.DOTALL)
_ITALIC_UNDER_RE = re.compile(r"(?<![\w_])_(?=[^\s_])([^_]*[^\s_])_(?![\w_])", re.DOTALL)

# Inline code: `code` and ``code`` — content kept.
_INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1", re.DOTALL)

# Links: [text](url) → text (url) when text != url, else url.
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# ATX headers: leading #'s (space after), optional closing #'s.
_HEADER_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$", re.MULTILINE)

# Blockquote markers (possibly nested ">>> ").
_QUOTE_RE = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.MULTILINE)

# Backslash escapes of ASCII punctuation.
_ESCAPE_RE = re.compile(r"\\([!\"#$%&'()*+,./:;<=>?@[\\\]^_`{|}~])")

# An indented code block's blank lines collapse to nothing after quote
# stripping; tidy runs of ≥3 newlines back to a double newline.
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def strip_markdown(text: str) -> str:
    """Convert markdown `text` to plain text.

    Coverage and invariants are specified in openspec
    `plain-text-output` capability; see module docstring. Pure and
    idempotent.
    """
    if not text:
        return text

    # \x00 never appears in chat text; scrub defensively so placeholders
    # cannot collide with user content.
    out = text.replace("\x00", "")

    # Fences first — bodies are replaced with placeholders so later passes
    # cannot strip inside them, then restored verbatim.
    bodies: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        bodies.append(m.group(2) or "")
        return f"\x00{len(bodies) - 1}\x00"

    out = _FENCE_RE.sub(_stash, out)

    out = _LINK_RE.sub(_link_repl, out)
    out = _BOLD_RE.sub(r"\1", out)
    out = _BOLD_UNDER_RE.sub(r"\1", out)
    out = _STRIKE_RE.sub(r"\1", out)
    out = _ITALIC_STAR_RE.sub(r"\1", out)
    out = _ITALIC_UNDER_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(lambda m: m.group(2), out)
    out = _HEADER_RE.sub(lambda m: m.group(2), out)
    out = _QUOTE_RE.sub("", out)
    out = _ESCAPE_RE.sub(r"\1", out)

    for i, body in enumerate(bodies):
        out = out.replace(f"\x00{i}\x00", body)

    out = _BLANK_RUN_RE.sub("\n\n", out)
    return out.strip("\n").strip() if out.strip() else out.strip()


def _link_repl(m: re.Match[str]) -> str:
    label, url = m.group(1), m.group(2)
    label = label.strip()
    if label == url or not label:
        return url
    return f"{label} ({url})"

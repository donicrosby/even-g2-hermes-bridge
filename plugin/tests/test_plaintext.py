"""Tests for byoa_plugin.plaintext.strip_markdown.

Pins the coverage set and safety invariants from the openspec
`plain-text-output` capability: constructs stripped, constructs left
alone, intra-word punctuation safety, idempotency.
"""

from __future__ import annotations

from byoa_plugin.plaintext import strip_markdown


class TestEmphasis:
    def test_bold_star(self) -> None:
        assert strip_markdown("Set **temp** to 21") == "Set temp to 21"

    def test_bold_underscore(self) -> None:
        assert strip_markdown("__bold__ words") == "bold words"

    def test_italic_star(self) -> None:
        assert strip_markdown("an *italic* word") == "an italic word"

    def test_italic_underscore(self) -> None:
        assert strip_markdown("an _italic_ word") == "an italic word"

    def test_strike(self) -> None:
        assert strip_markdown("~~gone~~ kept") == "gone kept"

    def test_bold_italic_nest(self) -> None:
        assert strip_markdown("**bold *nested* end**") == "bold nested end"

    def test_intra_word_asterisks_unchanged(self) -> None:
        assert strip_markdown("Multiply 2*3*4") == "Multiply 2*3*4"

    def test_glob_unchanged(self) -> None:
        assert strip_markdown("rm *.py") == "rm *.py"

    def test_lone_marker_unchanged(self) -> None:
        assert strip_markdown("a * b") == "a * b"

    def test_underscore_words_unchanged(self) -> None:
        # snake_case and __dunder__ style names are not emphasis here
        # because of word-char flanking guards... __dunder__ IS matched by
        # bold-under (it is flanked). Dunder is rare in chat; accepted.
        assert strip_markdown("file_name_here") == "file_name_here"


class TestCode:
    def test_inline_code(self) -> None:
        assert strip_markdown("run `uv run pytest` now") == "run uv run pytest now"

    def test_inline_code_double_backtick(self) -> None:
        assert strip_markdown("``a ` b``") == "a ` b"

    def test_fenced_code_block(self) -> None:
        text = "before\n```python\nprint('hi')\n```\nafter"
        assert strip_markdown(text) == "before\nprint('hi')\nafter"

    def test_fence_body_preserved_verbatim(self) -> None:
        body = "**not stripped** `kept`\n*kept*"
        text = f"```\n{body}\n```"
        assert strip_markdown(text) == body

    def test_fence_with_info_string(self) -> None:
        text = "```bash\nls -la\n```"
        assert strip_markdown(text) == "ls -la"

    def test_tilde_fence(self) -> None:
        text = "~~~\nplain\n~~~"
        assert strip_markdown(text) == "plain"


class TestLinks:
    def test_link_text_differs_from_url(self) -> None:
        assert strip_markdown("See [docs](https://example.com)") == "See docs (https://example.com)"

    def test_link_url_only(self) -> None:
        assert strip_markdown("[https://example.com](https://example.com)") == "https://example.com"

    def test_bare_url_untouched(self) -> None:
        assert strip_markdown("go to https://example.com now") == "go to https://example.com now"


class TestHeaders:
    def test_h1(self) -> None:
        assert strip_markdown("# Title") == "Title"

    def test_h3(self) -> None:
        assert strip_markdown("### Deep") == "Deep"

    def test_header_with_closing(self) -> None:
        assert strip_markdown("## Title ##") == "Title"

    def test_header_mid_text(self) -> None:
        assert strip_markdown("intro\n# Title\nbody") == "intro\nTitle\nbody"

    def test_hash_not_header(self) -> None:
        assert strip_markdown("use #hashtag here") == "use #hashtag here"


class TestQuotesAndEscapes:
    def test_blockquote(self) -> None:
        assert strip_markdown("> quoted line") == "quoted line"

    def test_nested_blockquote(self) -> None:
        assert strip_markdown(">> deep") == "deep"

    def test_backslash_escape(self) -> None:
        assert strip_markdown(r"literal \* not emphasis") == "literal * not emphasis"

    def test_escaped_pipe(self) -> None:
        assert strip_markdown(r"a \| b") == "a | b"


class TestLeftAlone:
    def test_bullets(self) -> None:
        assert strip_markdown("- item one\n- item two") == "- item one\n- item two"

    def test_table(self) -> None:
        text = "| a | b |\n| - | - |\n| 1 | 2 |"
        assert strip_markdown(text) == text

    def test_horizontal_rule(self) -> None:
        assert strip_markdown("above\n---\nbelow") == "above\n---\nbelow"


class TestInvariants:
    def test_idempotent(self) -> None:
        samples = [
            "Set **temp** to 21 and `run` it",
            "# Head\n\n- a\n- b\n\n> quote",
            "See [docs](https://example.com) ~~now~~",
            "```py\nx = 1\n```",
            "Multiply 2*3*4",
        ]
        for s in samples:
            once = strip_markdown(s)
            assert strip_markdown(once) == once, s

    def test_empty(self) -> None:
        assert strip_markdown("") == ""

    def test_plain_text_unchanged(self) -> None:
        assert strip_markdown("just plain words") == "just plain words"

    def test_multiline_prose(self) -> None:
        text = "**Deploy note**\n\n1. run `make build`\n2. see [docs](http://d.x/y)\n"
        assert strip_markdown(text) == "Deploy note\n\n1. run make build\n2. see docs (http://d.x/y)"

import unittest

from codex_nomad_surface.markdown_rendering import markdown_with_soft_line_breaks


class MarkdownRenderingTests(unittest.TestCase):
    def test_single_newline_becomes_hard_break(self) -> None:
        self.assertEqual(markdown_with_soft_line_breaks("alpha\nbeta"), "alpha  \nbeta")

    def test_blank_line_keeps_paragraph_break(self) -> None:
        self.assertEqual(
            markdown_with_soft_line_breaks("alpha\n\nbeta"),
            "alpha\n\nbeta",
        )

    def test_fenced_code_block_is_not_modified(self) -> None:
        source = "before\n```python\nprint('a')\nprint('b')\n```\nafter"

        self.assertEqual(
            markdown_with_soft_line_breaks(source),
            source,
        )

    def test_fence_with_trailing_text_does_not_close_block(self) -> None:
        source = "```\n```not a closing fence\ninside\n```\nafter\nmore"

        self.assertEqual(
            markdown_with_soft_line_breaks(source),
            "```\n```not a closing fence\ninside\n```\nafter  \nmore",
        )

    def test_table_is_not_modified(self) -> None:
        source = "a | b\n--- | ---\n1 | 2"

        self.assertEqual(markdown_with_soft_line_breaks(source), source)

    def test_heading_boundary_is_not_modified(self) -> None:
        source = "# Heading\nbody"

        self.assertEqual(markdown_with_soft_line_breaks(source), source)

    def test_indented_code_block_is_not_modified(self) -> None:
        source = "before\n    code\n    still code\nafter"

        self.assertEqual(markdown_with_soft_line_breaks(source), source)

    def test_html_block_is_not_modified(self) -> None:
        source = "before\n<pre>\na\nb\n</pre>\nafter"

        self.assertEqual(
            markdown_with_soft_line_breaks(source),
            source,
        )

    def test_existing_hard_break_is_not_rewritten(self) -> None:
        source = "alpha  \nbeta\\\ngamma"

        self.assertEqual(markdown_with_soft_line_breaks(source), source)


if __name__ == "__main__":
    unittest.main()

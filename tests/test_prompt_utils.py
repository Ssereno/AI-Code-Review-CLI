"""
Tests for prompt_utils.py - language/extension based filtering of the custom review prompt.
"""
from src.prompt_utils import detect_langs, filter_prompt_by_langs

class TestDetectLangs:

    def test_always_includes_all(self) -> None:
        """'all' must always be present, even with no files."""
        assert detect_langs([]) == {"all"}

    def test_detects_single_extension(self) -> None:
        result = detect_langs(["src/foo.py"])
        assert result == {"all", "py"}

    def test_detects_multiple_distinct_extensions(self) -> None:
        result = detect_langs(["a.ts", "b.html", "c.cs"])
        assert result == {"all", "ts", "html", "cs"}

    def test_deduplicates_same_extension(self) -> None:
        result = detect_langs(["a.py", "b.py", "c.py"])
        assert result == {"all", "py"}

    def test_extension_lowercased(self) -> None:
        result = detect_langs(["Component.TS"])
        assert result == {"all", "ts"}

    def test_ignores_none_or_empty_paths(self) -> None:
        result = detect_langs([None, "", "a.py"])
        assert result == {"all", "py"}

    def test_file_without_extension_is_ignored(self) -> None:
        result = detect_langs(["Dockerfile", "Makefile"])
        assert result == {"all"}

    def test_nested_path_extracts_extension_correctly(self) -> None:
        result = detect_langs(["src/app/components/foo.component.ts"])
        assert result == {"all", "ts"}

    def test_dotfile_with_no_further_extension_is_ignored(self) -> None:
        # os.path.splitext(".gitignore") -> ('.gitignore', '') -> no extension detected
        result = detect_langs([".gitignore"])
        assert result == {"all"}


class TestFilterPromptByLangs:

    def test_content_without_any_tag_is_kept_entirely(self) -> None:
        content = "Always mention tests"
        result = filter_prompt_by_langs(content, {"all"})
        assert result == "Always mention tests"

    def test_all_section_is_always_included(self) -> None:
        content = (
            "<!-- lang: all -->\n"
            "## General\n"
            "- Rule A\n"
        )
        result = filter_prompt_by_langs(content, {"all", "cs"})
        assert "## General" in result
        assert "Rule A" in result

    def test_matching_lang_section_is_included(self) -> None:
        content = (
            "<!-- lang: cs,ts -->\n"
            "## Dependencies\n"
            "- Rule B\n"
        )
        result = filter_prompt_by_langs(content, {"all", "ts"})
        assert "## Dependencies" in result
        assert "Rule B" in result

    def test_non_matching_lang_section_is_excluded(self) -> None:
        content = (
            "<!-- lang: java -->\n"
            "## Java Rules\n"
            "- Rule C\n"
        )
        result = filter_prompt_by_langs(content, {"all", "ts"})
        assert "Java Rules" not in result
        assert "Rule C" not in result

    def test_mixed_sections_filters_correctly(self) -> None:
        content = (
            "<!-- lang: all -->\n"
            "## General\n"
            "- General rule\n"
            "\n"
            "<!-- lang: cs -->\n"
            "## CSharp\n"
            "- CSharp rule\n"
            "\n"
            "<!-- lang: html -->\n"
            "## Html\n"
            "- Html rule\n"
        )
        result = filter_prompt_by_langs(content, {"all", "html"})

        assert "General rule" in result
        assert "Html rule" in result
        assert "CSharp rule" not in result
        assert "CSharp" not in result

    def test_tags_are_case_insensitive(self) -> None:
        content = (
            "<!-- lang: CS,TS -->\n"
            "## Dependencies\n"
            "- Rule\n"
        )
        result = filter_prompt_by_langs(content, {"all", "cs"})
        assert "Rule" in result

    def test_no_matching_section_returns_empty(self) -> None:
        content = (
            "<!-- lang: java -->\n"
            "## Java Rules\n"
            "- Rule\n"
        )
        result = filter_prompt_by_langs(content, {"all", "ts"})
        assert result == ""

    def test_tag_with_extra_whitespace_is_parsed(self) -> None:
        content = (
            "<!--  lang:   cs , ts  -->\n"
            "## Dependencies\n"
            "- Rule\n"
        )
        result = filter_prompt_by_langs(content, {"all", "ts"})
        assert "Rule" in result
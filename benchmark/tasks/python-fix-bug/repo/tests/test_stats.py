"""Public tests for textstats."""

from textstats import count_words, most_common_word, word_frequencies


def test_count_words_basic() -> None:
    assert count_words("hello world") == 2


def test_count_words_empty() -> None:
    assert count_words("") == 0


def test_word_frequencies() -> None:
    assert word_frequencies("a b a") == {"a": 2, "b": 1}


def test_most_common_word() -> None:
    assert most_common_word("the cat the dog the") == "the"


def test_most_common_word_empty() -> None:
    assert most_common_word("") == ""

"""Hidden edge-case tests injected by the grader."""

from textstats import most_common_word


def test_tie_breaks_by_first_appearance() -> None:
    assert most_common_word("b a b a") == "b"


def test_tie_three_way() -> None:
    assert most_common_word("x y z x y z") == "x"


def test_clear_winner_after_tie() -> None:
    assert most_common_word("a b a b b") == "b"


def test_single_word() -> None:
    assert most_common_word("solo") == "solo"


def test_case_insensitive() -> None:
    assert most_common_word("The the THE cat") == "the"


def test_punctuation_ignored() -> None:
    assert most_common_word("hi, hi! bye?") == "hi"

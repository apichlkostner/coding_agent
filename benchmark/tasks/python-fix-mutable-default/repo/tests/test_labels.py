"""Public tests for labels."""

from labels import append_label


def test_append_to_explicit_list() -> None:
    labels = ["a"]
    result = append_label("b", labels)
    assert result == ["a", "b"]


def test_explicit_list_is_mutated_in_place() -> None:
    labels = ["a"]
    result = append_label("b", labels)
    assert result is labels
    assert labels == ["a", "b"]


def test_omitted_argument_starts_empty() -> None:
    assert append_label("first") == ["first"]


def test_omitted_argument_is_isolated() -> None:
    first = append_label("first")
    second = append_label("second")
    assert first == ["first"]
    assert second == ["second"]

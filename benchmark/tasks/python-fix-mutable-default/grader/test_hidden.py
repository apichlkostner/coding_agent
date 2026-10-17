"""Hidden edge-case tests injected by the grader."""

from labels import append_label


def test_repeated_omitted_calls_are_independent() -> None:
    first = append_label("a")
    second = append_label("b")
    third = append_label("c")
    assert first == ["a"]
    assert second == ["b"]
    assert third == ["c"]


def test_omitted_calls_return_distinct_objects() -> None:
    first = append_label("a")
    second = append_label("b")
    assert first is not second


def test_explicit_list_identity_and_mutation() -> None:
    labels: list[str] = []
    result = append_label("x", labels)
    assert result is labels
    assert labels == ["x"]


def test_explicit_list_accumulates_across_calls() -> None:
    labels = ["start"]
    append_label("one", labels)
    append_label("two", labels)
    assert labels == ["start", "one", "two"]


def test_positional_invocation() -> None:
    labels = ["a"]
    assert append_label("b", labels) is labels
    assert labels == ["a", "b"]


def test_keyword_invocation() -> None:
    labels = ["a"]
    assert append_label(label="b", labels=labels) is labels
    assert labels == ["a", "b"]


def test_keyword_omitted_argument() -> None:
    assert append_label(label="solo") == ["solo"]


def test_omitted_after_explicit_does_not_leak() -> None:
    explicit = ["a"]
    append_label("b", explicit)
    fresh = append_label("c")
    assert explicit == ["a", "b"]
    assert fresh == ["c"]

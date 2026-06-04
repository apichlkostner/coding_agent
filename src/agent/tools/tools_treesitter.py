"""Tree-sitter tools for the coding agent.

Provides three LangChain tools that expose tree-sitter syntax-tree
functionality to the ReAct agent:

- ``treesitter_parse``       — parse a source file or code string into a
                               JSON representation of the syntax tree.
- ``treesitter_query``       — run an S-expression query against a source
                               file or code string and return captures.
- ``treesitter_get_symbols`` — extract top-level symbols (functions, classes,
                               imports) from a source file using built-in
                               per-language queries.

Supported languages
-------------------
python, javascript, typescript, tsx, rust, go, c, cpp

All file-based inputs are restricted to paths inside the project working
directory (the same policy as ``tools_filesystem.py``).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from agent.tools.tools_filesystem import _is_subpath

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_CHAR_LIMIT = 8_000
LEAF_TEXT_LIMIT = 120

# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

# Maps canonical language name → callable that returns the language capsule.
# Using lambdas defers the imports, so a missing optional package only fails
# at the point the language is actually requested, not at module import time.
_LANGUAGE_FACTORIES: dict[str, Any] = {}


def _register_languages() -> None:
    """Populate ``_LANGUAGE_FACTORIES`` for all installed grammar packages."""
    try:
        import tree_sitter_python as _tspy  # noqa: PLC0415

        _LANGUAGE_FACTORIES["python"] = _tspy.language
    except ImportError:
        pass

    try:
        import tree_sitter_javascript as _tsjs  # noqa: PLC0415

        _LANGUAGE_FACTORIES["javascript"] = _tsjs.language
    except ImportError:
        pass

    try:
        import tree_sitter_typescript as _tsts  # noqa: PLC0415

        _LANGUAGE_FACTORIES["typescript"] = _tsts.language_typescript
        _LANGUAGE_FACTORIES["tsx"] = _tsts.language_tsx
    except ImportError:
        pass

    try:
        import tree_sitter_rust as _tsrs  # noqa: PLC0415

        _LANGUAGE_FACTORIES["rust"] = _tsrs.language
    except ImportError:
        pass

    try:
        import tree_sitter_go as _tsgo  # noqa: PLC0415

        _LANGUAGE_FACTORIES["go"] = _tsgo.language
    except ImportError:
        pass

    try:
        import tree_sitter_c as _tsc  # noqa: PLC0415

        _LANGUAGE_FACTORIES["c"] = _tsc.language
    except ImportError:
        pass

    try:
        import tree_sitter_cpp as _tscpp  # noqa: PLC0415

        _LANGUAGE_FACTORIES["cpp"] = _tscpp.language
    except ImportError:
        pass


_register_languages()

# Maps file extension (without leading dot, lower-cased) → canonical language name.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "tsx": "tsx",
    "rs": "rust",
    "go": "go",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hxx": "cpp",
}

# ---------------------------------------------------------------------------
# Pre-built symbol queries, one per language.
# Each query must capture:
#   @symbol  — the full node for the top-level declaration
#   @name    — the identifier node for named declarations (optional for imports)
# ---------------------------------------------------------------------------

_SYMBOL_QUERIES: dict[str, str] = {
    "python": """
        [
          (function_definition name: (identifier) @name)
          (class_definition name: (identifier) @name)
          (decorated_definition
            [
              (function_definition name: (identifier) @name)
              (class_definition name: (identifier) @name)
            ])
          (import_statement)
          (import_from_statement)
        ] @symbol
    """,
    "javascript": """
        [
          (function_declaration name: (identifier) @name)
          (class_declaration name: (identifier) @name)
          (lexical_declaration
            (variable_declarator name: (identifier) @name))
          (import_statement)
          (export_statement)
        ] @symbol
    """,
    "typescript": """
        [
          (function_declaration name: (identifier) @name)
          (class_declaration name: (type_identifier) @name)
          (interface_declaration name: (type_identifier) @name)
          (type_alias_declaration name: (type_identifier) @name)
          (import_statement)
          (export_statement)
        ] @symbol
    """,
    "tsx": """
        [
          (function_declaration name: (identifier) @name)
          (class_declaration name: (type_identifier) @name)
          (interface_declaration name: (type_identifier) @name)
          (type_alias_declaration name: (type_identifier) @name)
          (import_statement)
          (export_statement)
        ] @symbol
    """,
    "rust": """
        [
          (function_item name: (identifier) @name)
          (struct_item name: (type_identifier) @name)
          (enum_item name: (type_identifier) @name)
          (trait_item name: (type_identifier) @name)
          (type_item name: (type_identifier) @name)
          (use_declaration)
        ] @symbol
    """,
    "go": """
        [
          (function_declaration name: (identifier) @name)
          (method_declaration name: (field_identifier) @name)
          (type_declaration)
          (import_declaration)
        ] @symbol
    """,
    "c": """
        [
          (function_definition
            declarator: (function_declarator
              declarator: (identifier) @name))
          (struct_specifier name: (type_identifier) @name)
          (enum_specifier name: (type_identifier) @name)
          (preproc_include)
        ] @symbol
    """,
    "cpp": """
        [
          (function_definition
            declarator: (function_declarator
              declarator: (identifier) @name))
          (class_specifier name: (type_identifier) @name)
          (struct_specifier name: (type_identifier) @name)
          (enum_specifier name: (type_identifier) @name)
          (preproc_include)
        ] @symbol
    """,
}

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _language_for_extension(ext: str) -> str | None:
    """Return the canonical language name for a file extension, or ``None``."""
    return _EXTENSION_TO_LANGUAGE.get(ext.lstrip(".").lower())


@functools.lru_cache(maxsize=32)
def _get_language(language_name: str) -> Language:
    """Return a cached ``Language`` object for the given canonical name.

    Raises
    ------
    ValueError
        If the language name is not recognised or its grammar package is not
        installed.
    """
    factory = _LANGUAGE_FACTORIES.get(language_name)
    if factory is None:
        available = sorted(_LANGUAGE_FACTORIES.keys())
        raise ValueError(
            f"Unsupported language: '{language_name}'. Available: {available}"
        )
    return Language(factory())


@functools.lru_cache(maxsize=32)
def _get_parser(language_name: str) -> Parser:
    """Return a cached ``Parser`` configured for the given language."""
    return Parser(_get_language(language_name))


def _resolve_source(
    path: str | None,
    code: str | None,
    language: str | None,
) -> tuple[bytes, str]:
    """Resolve the source bytes and language name from the provided inputs.

    Parameters
    ----------
    path:
        Path to a source file.  Mutually exclusive with ``code``.
    code:
        Inline source code string.  Requires ``language`` to be specified.
    language:
        Explicit language override.  If omitted, the language is inferred
        from the file extension when ``path`` is given.

    Returns
    -------
    tuple[bytes, str]
        ``(source_bytes, resolved_language_name)``

    Raises
    ------
    ValueError
        On missing arguments, unknown extension, or path-safety violations.
    """
    if path is not None and code is not None:
        raise ValueError("Provide either 'path' or 'code', not both.")
    if path is None and code is None:
        raise ValueError("Either 'path' or 'code' must be provided.")

    if path is not None:
        if not _is_subpath(path, strict=True):
            raise ValueError("Path is not inside the project folder.")
        source_bytes = Path(path).read_bytes()
        if language is None:
            ext = Path(path).suffix
            language = _language_for_extension(ext)
            if language is None:
                raise ValueError(
                    f"Cannot detect language from extension '{ext}'. "
                    "Specify the 'language' parameter explicitly."
                )
    else:
        # code is not None
        source_bytes = code.encode("utf-8")  # type: ignore[union-attr]
        if language is None:
            raise ValueError(
                "The 'language' parameter is required when 'code' is provided."
            )

    # Validate that the language is supported (raises ValueError if not).
    _get_language(language)

    return source_bytes, language


def _fit_list_to_limit(items: list[Any]) -> list[Any]:
    """Return a JSON-serializable list that fits within ``OUTPUT_CHAR_LIMIT``.

    If the full serialization exceeds the limit, items are removed from the
    end and a sentinel ``{"truncated": true, "omitted_count": N}`` is
    appended so the result is always valid JSON.
    """
    if not items:
        return items
    if len(json.dumps(items, indent=2)) <= OUTPUT_CHAR_LIMIT:
        return items

    # Binary-search for the largest prefix that fits with the sentinel.
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = items[:mid] + [
            {"truncated": True, "omitted_count": len(items) - mid}
        ]
        if len(json.dumps(candidate, indent=2)) <= OUTPUT_CHAR_LIMIT:
            lo = mid
        else:
            hi = mid - 1

    return items[:lo] + [{"truncated": True, "omitted_count": len(items) - lo}]


def _fit_tree_to_limit(node: Node, max_depth: int) -> str:
    """Serialize a tree node to JSON within ``OUTPUT_CHAR_LIMIT``.

    If the serialization at ``max_depth`` exceeds the limit, the depth is
    reduced by one and retried until the output fits.  A
    ``"_depth_reduced_to"`` key is added to the root object when the depth
    had to be lowered.  The return value is always valid JSON.
    """
    for depth in range(max_depth, -1, -1):
        tree_dict = _node_to_dict(node, depth)
        if depth < max_depth:
            tree_dict["_depth_reduced_to"] = depth
        output = json.dumps(tree_dict, indent=2)
        if len(output) <= OUTPUT_CHAR_LIMIT:
            return output
    # Depth 0 is bounded by LEAF_TEXT_LIMIT so this is effectively unreachable.
    return output  # type: ignore[return-value]


def _node_to_dict(node: Node, max_depth: int, current_depth: int = 0) -> dict[str, Any]:
    """Recursively convert a tree-sitter ``Node`` to a plain dictionary.

    Leaf nodes include a ``"text"`` key (truncated to ``LEAF_TEXT_LIMIT``
    characters).  Recursion stops when ``current_depth >= max_depth``.
    """
    result: dict[str, Any] = {
        "type": node.type,
        "start": list(node.start_point),
        "end": list(node.end_point),
    }

    named_children = node.named_children
    if not named_children or current_depth >= max_depth:
        raw = node.text
        if raw is not None:
            text = raw.decode("utf-8", errors="replace")
            if len(text) > LEAF_TEXT_LIMIT:
                text = text[:LEAF_TEXT_LIMIT] + "..."
            result["text"] = text
    else:
        result["children"] = [
            _node_to_dict(child, max_depth, current_depth + 1)
            for child in named_children
        ]

    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def treesitter_parse(
    path: str | None = None,
    code: str | None = None,
    language: str | None = None,
    max_depth: int = 5,
) -> str:
    """Parse a source file or inline code string into a JSON syntax tree.

    The syntax tree is represented as nested JSON objects.  Each node
    contains its ``type``, ``start`` and ``end`` positions (as
    ``[row, column]``), and either a ``children`` list (for branch nodes up
    to ``max_depth``) or a ``text`` field truncated to 120 characters (for
    leaf nodes or nodes at the depth limit).

    The total output is capped at 8 000 characters to protect the LLM
    context window.  Increase ``max_depth`` for deeper trees; decrease it
    for large files.

    Parameters
    ----------
    path:
        Path to the source file to parse (must be inside the project root).
        Mutually exclusive with ``code``.
    code:
        Inline source code string.  Requires ``language`` to be specified.
        Mutually exclusive with ``path``.
    language:
        Canonical language name: python, javascript, typescript, tsx, rust,
        go, c, or cpp.  Inferred from the file extension when ``path`` is
        given; required when ``code`` is given.
    max_depth:
        Maximum tree depth to expand.  Nodes beyond this depth are rendered
        as leaf nodes with their full text.  Default: 5.

    Returns
    -------
    str
        Indented JSON string representing the syntax tree, or an
        ``"Error: ..."`` string on failure.

    Examples
    --------
    treesitter_parse(path="src/agent/tools.py")
    treesitter_parse(code="def foo(): pass", language="python", max_depth=3)
    """
    try:
        source_bytes, lang_name = _resolve_source(path, code, language)
        parser = _get_parser(lang_name)
        tree = parser.parse(source_bytes)
        return _fit_tree_to_limit(tree.root_node, max_depth)
    except Exception as exc:
        return f"Error: {exc}"


@tool
def treesitter_query(
    query_pattern: str,
    path: str | None = None,
    code: str | None = None,
    language: str | None = None,
) -> str:
    """Run a tree-sitter S-expression query against a source file or code string.

    The query pattern uses the standard tree-sitter query syntax.  Named
    captures (``@capture_name``) are returned in the result.

    Each match in the result is a JSON object:

    .. code-block:: json

        {
          "pattern_index": 0,
          "captures": {
            "fn_name": {
              "type": "identifier",
              "text": "my_function",
              "start": [3, 4],
              "end": [3, 15]
            }
          }
        }

    When a capture name matches multiple nodes in a single pattern match,
    only the first node is included.

    Parameters
    ----------
    query_pattern:
        An S-expression tree-sitter query string.  Example for Python
        function names::

            (function_definition name: (identifier) @fn_name)

    path:
        Path to the source file (must be inside the project root).
        Mutually exclusive with ``code``.
    code:
        Inline source code string.  Requires ``language`` to be specified.
        Mutually exclusive with ``path``.
    language:
        Canonical language name.  Inferred from extension when ``path`` is
        given; required when ``code`` is given.

    Returns
    -------
    str
        JSON array of match objects, or an ``"Error: ..."`` string on
        invalid query or parse failure.

    Examples
    --------
    treesitter_query(
        "(function_definition name: (identifier) @fn_name)",
        path="src/agent/tools.py",
    )
    """
    try:
        source_bytes, lang_name = _resolve_source(path, code, language)
        lang = _get_language(lang_name)
        parser = _get_parser(lang_name)
        tree = parser.parse(source_bytes)

        try:
            query = Query(lang, query_pattern)
        except Exception as exc:
            return f"Error: Invalid query pattern — {exc}"

        cursor = QueryCursor(query)
        raw_matches = cursor.matches(tree.root_node)

        results = []
        for pattern_index, capture_dict in raw_matches:
            captures: dict[str, dict[str, Any]] = {}
            for capture_name, nodes in capture_dict.items():
                if not nodes:
                    continue
                node = nodes[0]
                raw_text = node.text
                text = (
                    raw_text.decode("utf-8", errors="replace")
                    if raw_text is not None
                    else ""
                )
                if len(text) > LEAF_TEXT_LIMIT:
                    text = text[:LEAF_TEXT_LIMIT] + "..."
                captures[capture_name] = {
                    "type": node.type,
                    "text": text,
                    "start": list(node.start_point),
                    "end": list(node.end_point),
                }
            results.append({"pattern_index": pattern_index, "captures": captures})

        return json.dumps(_fit_list_to_limit(results), indent=2)
    except Exception as exc:
        return f"Error: {exc}"


@tool
def treesitter_get_symbols(
    path: str | None = None,
    code: str | None = None,
    language: str | None = None,
) -> str:
    """Extract top-level symbols from a source file or code string.

    Uses a built-in per-language tree-sitter query to find function
    definitions, class definitions, and import statements at the top level
    of the file.  This is the most LLM-friendly of the three tree-sitter
    tools: it returns a compact, flat list rather than a full syntax tree.

    Each symbol in the result is a JSON object::

        {
          "kind": "function_definition",
          "name": "my_function",
          "start_line": 10,
          "end_line": 20
        }

    Line numbers are 1-based (matching editor and grep conventions).

    Only declarations directly under the file root are returned; nested
    declarations (e.g. inner functions or local classes) are excluded.

    For import/include nodes where no named identifier is captured, the
    ``"name"`` field contains the full source text of the node (truncated
    to 120 characters).

    Parameters
    ----------
    path:
        Path to the source file (must be inside the project root).
        Mutually exclusive with ``code``.
    code:
        Inline source code string.  Requires ``language`` to be specified.
        Mutually exclusive with ``path``.
    language:
        Canonical language name: python, javascript, typescript, tsx, rust,
        go, c, or cpp.  Inferred from file extension when ``path`` is given;
        required when ``code`` is given.

    Returns
    -------
    str
        JSON array of symbol objects, or an ``"Error: ..."`` string on
        failure.

    Examples
    --------
    treesitter_get_symbols(path="src/agent/tools.py")
    treesitter_get_symbols(code="fn main() {}", language="rust")
    """
    try:
        source_bytes, lang_name = _resolve_source(path, code, language)

        query_str = _SYMBOL_QUERIES.get(lang_name)
        if query_str is None:
            return f"Error: no symbol query defined for language '{lang_name}'"

        lang = _get_language(lang_name)
        parser = _get_parser(lang_name)
        tree = parser.parse(source_bytes)

        root = tree.root_node
        query = Query(lang, query_str)
        cursor = QueryCursor(query)
        raw_matches = cursor.matches(root)

        symbols: list[Any] = []
        seen: set[tuple[int, int]] = set()

        for _pattern_index, capture_dict in raw_matches:
            symbol_nodes = capture_dict.get("symbol", [])
            name_nodes = capture_dict.get("name", [])

            for symbol_node in symbol_nodes:
                # Only include declarations directly under the file root.
                if symbol_node.parent != root:
                    continue

                key = (symbol_node.start_byte, symbol_node.end_byte)
                if key in seen:
                    continue
                seen.add(key)

                if name_nodes:
                    raw_text = name_nodes[0].text
                    name = (
                        raw_text.decode("utf-8", errors="replace")
                        if raw_text is not None
                        else ""
                    )
                else:
                    # Import/include nodes: use the full node text as the name.
                    raw_text = symbol_node.text
                    name = (
                        raw_text.decode("utf-8", errors="replace")
                        if raw_text is not None
                        else ""
                    )
                    if len(name) > LEAF_TEXT_LIMIT:
                        name = name[:LEAF_TEXT_LIMIT] + "..."

                symbols.append(
                    {
                        "kind": symbol_node.type,
                        "name": name,
                        "start_line": symbol_node.start_point[0] + 1,
                        "end_line": symbol_node.end_point[0] + 1,
                    }
                )

        return json.dumps(_fit_list_to_limit(symbols), indent=2)
    except Exception as exc:
        return f"Error: {exc}"

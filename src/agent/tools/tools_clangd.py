"""clangd-powered LSP tools for the coding agent.

Eight LangChain ``@tool`` functions that wrap the :class:`agent.lsp.ClangdClient`
singleton and expose its features to the ReAct agent:

- ``clangd_completion``         — list code completions at a cursor position.
- ``clangd_definition``         — jump to the definition of a symbol.
- ``clangd_references``         — find all references to a symbol.
- ``clangd_document_symbols``   — list symbols defined in a single file.
- ``clangd_workspace_symbols``  — search symbols across the workspace.
- ``clangd_rename``             — return a ``WorkspaceEdit`` renaming a symbol.
- ``clangd_type_hierarchy``     — explore supertypes / subtypes of a type.
- ``clangd_call_hierarchy``     — explore incoming / outgoing calls of a function.

All tools return a JSON string and catch every exception into a
``"Error: ..."`` payload so the LLM can react gracefully. Filesystem paths
are gated by :func:`agent.tools.tools_filesystem._is_subpath` — paths
outside the project root are rejected.

Position conventions
--------------------
Inputs use **1-based line numbers** and **0-based character offsets**,
matching ``treesitter_get_symbols`` and editor grep conventions.
Outputs convert LSP's 0-based positions to **1-based line numbers** for
consistency.

Performance
-----------
The clangd subprocess is shared via a process-wide singleton (see
:mod:`agent.lsp.client`) and started lazily on the first tool call.
The first invocation may take a few seconds; subsequent calls are fast.
"""

from __future__ import annotations

import json
from typing import Any, cast

from langchain_core.tools import tool

from agent.lsp import ClangdClient, get_default_client
from agent.tools.tools_filesystem import _is_subpath

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_CHAR_LIMIT = 8_000

_VALID_TYPE_HIERARCHY_DIRS = ("subtypes", "supertypes", "both")
_VALID_CALL_HIERARCHY_DIRS = ("outgoing", "incoming", "both")

# LSP SymbolKind -> human-readable string. Subset — the kinds the LLM is
# most likely to encounter and reason about.
_SYMBOL_KIND_NAMES: dict[int, str] = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_path(path: str) -> str | None:
    """Validate *path* is inside the project root. Return absolute path or None."""
    if not _is_subpath(path, strict=True):
        return None
    return str(path)


def _line_col_to_0based(line: int, character: int) -> tuple[int, int]:
    """Convert (1-based line, 0-based character) to (0-based, 0-based)."""
    return max(0, int(line) - 1), max(0, int(character))


def _symbol_kind_name(kind: Any) -> str:
    if isinstance(kind, int):
        return _SYMBOL_KIND_NAMES.get(kind, str(kind))
    return str(kind)


def _format_locations(locations: list[Any]) -> list[dict[str, Any]]:
    """Convert LSP ``Location`` list to flat, 1-based line triples."""
    out: list[dict[str, Any]] = []
    for loc in locations or []:
        uri = loc.get("uri", "")
        rng = loc.get("range") or {}
        start = rng.get("start") or {}
        line = start.get("line", 0)
        char = start.get("character", 0)
        path = _uri_to_path_safe(uri)
        out.append(
            {
                "path": path,
                "line": int(line) + 1,
                "character": int(char),
            }
        )
    return out


def _format_symbols(symbols: list[Any]) -> list[dict[str, Any]]:
    """Convert ``SymbolInformation`` to flat dicts with 1-based line numbers."""
    out: list[dict[str, Any]] = []
    for sym in symbols or []:
        location = sym.get("location") or {}
        uri = location.get("uri", "")
        rng = location.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                "name": sym.get("name", ""),
                "kind": _symbol_kind_name(sym.get("kind")),
                "container_name": sym.get("containerName", ""),
                "path": _uri_to_path_safe(uri),
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
            }
        )
    return out


def _format_hierarchical_symbols(
    symbols: list[Any],
) -> list[dict[str, Any]]:
    """Recursively flatten hierarchical ``DocumentSymbol`` trees.

    Each entry carries a ``depth`` field (root = 0) and a ``path``/``line``
    pair in 1-based form.
    """
    out: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        rng = node.get("range") or {}
        start = rng.get("start") or {}
        uri_field = node.get("uri", "")
        path = _uri_to_path_safe(uri_field) if uri_field else ""
        out.append(
            {
                "name": node.get("name", ""),
                "kind": _symbol_kind_name(node.get("kind")),
                "detail": node.get("detail", ""),
                "depth": depth,
                "path": path,
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
            }
        )
        for child in node.get("children") or []:
            walk(child, depth + 1)

    for sym in symbols or []:
        walk(sym, 0)
    return out


def _format_completion_items(items: list[Any]) -> list[dict[str, Any]]:
    """Strip noisy fields from ``CompletionItem`` for LLM consumption."""
    out: list[dict[str, Any]] = []
    for it in items or []:
        out.append(
            {
                "label": it.get("label", ""),
                "kind": _symbol_kind_name(it.get("kind")),
                "detail": it.get("detail", ""),
                "insert_text": it.get("insertText", ""),
            }
        )
    return out


def _format_type_hierarchy_items(
    items: list[Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items or []:
        uri = it.get("uri", "")
        rng = it.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                "name": it.get("name", ""),
                "kind": _symbol_kind_name(it.get("kind")),
                "detail": it.get("detail", ""),
                "path": _uri_to_path_safe(uri),
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
            }
        )
    return out


def _format_call_edges(
    edges: list[Any], direction: str
) -> list[dict[str, Any]]:
    """Format incoming / outgoing call edges into a uniform shape."""
    out: list[dict[str, Any]] = []
    for edge in edges or []:
        if direction == "incoming":
            item = edge.get("from") or {}
            call_sites = edge.get("fromRanges") or []
            kind = "from"
            other = "to"
        else:
            item = edge.get("to") or {}
            call_sites = edge.get("fromRanges") or []
            kind = "to"
            other = "from"
        uri = item.get("uri", "")
        rng = item.get("selectionRange") or item.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                kind: {
                    "name": item.get("name", ""),
                    "kind": _symbol_kind_name(item.get("kind")),
                    "path": _uri_to_path_safe(uri),
                    "line": int(start.get("line", 0)) + 1,
                    "character": int(start.get("character", 0)),
                },
                f"{other}_ranges": [
                    {
                        "line": int((r.get("start") or {}).get("line", 0)) + 1,
                        "character": int((r.get("start") or {}).get("character", 0)),
                    }
                    for r in call_sites
                ],
            }
        )
    return out


def _uri_to_path_safe(uri: str) -> str:
    """Best-effort ``file://`` URI → local path. Empty on failure."""
    if not uri:
        return ""
    if not uri.startswith("file://"):
        return uri
    try:
        from agent.lsp import uri_to_path

        return uri_to_path(uri)
    except (ValueError, Exception):  # noqa: BLE001
        # Fall back to naive stripping if anything goes wrong.
        from urllib.parse import unquote
        from urllib.request import url2pathname

        return url2pathname(unquote(uri[len("file://") :]))


def _truncate_payload(obj: Any) -> str:
    """JSON-serialise *obj* and clamp to ``OUTPUT_CHAR_LIMIT``.

    A trailing ``{"truncated": true, "omitted_count": N}`` sentinel is
    appended to list-like payloads so the LLM knows the output is partial.
    """
    if isinstance(obj, list) and obj:
        text = json.dumps(obj, indent=2)
        if len(text) <= OUTPUT_CHAR_LIMIT:
            return text

        lo, hi = 0, len(obj)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = obj[:mid] + [
                {"truncated": True, "omitted_count": len(obj) - mid}
            ]
            if len(json.dumps(candidate, indent=2)) <= OUTPUT_CHAR_LIMIT:
                lo = mid
            else:
                hi = mid - 1

        return json.dumps(
            obj[:lo] + [{"truncated": True, "omitted_count": len(obj) - lo}],
            indent=2,
        )

    text = json.dumps(obj, indent=2)
    if len(text) <= OUTPUT_CHAR_LIMIT:
        return text
    return text[:OUTPUT_CHAR_LIMIT] + "\n... (truncated)"


async def _get_client() -> ClangdClient:
    """Return the process-wide clangd client (lazy start)."""
    return await get_default_client()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def clangd_completion(path: str, line: int, character: int) -> str:
    """List code completions at a cursor position in a C/C++ source file.

    Backed by ``clangd`` via the Language Server Protocol
    (``textDocument/completion``). The file is opened in clangd if it is
    not already open; results are best-effort when no
    ``compile_commands.json`` is present in the project root.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.
    line:
        1-based line number of the cursor position.
    character:
        0-based character offset within the line.

    Returns
    -------
    str
        JSON array of completion items, each with ``label``, ``kind``,
        ``detail`` and ``insert_text``. Returns ``"Error: ..."`` on
        failure (e.g. clangd not installed).

    Examples
    --------
    clangd_completion("src/main.cpp", 10, 5)
    """
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        client = await _get_client()
        result = await client.completion(resolved, lsp_line, lsp_char)
        items = _format_completion_items(result.get("items", []))
        return _truncate_payload(items)
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_definition(path: str, line: int, character: int) -> str:
    """Jump to the definition of the symbol at the given cursor position.

    Backed by ``clangd`` (``textDocument/definition``). Returns the
    source location(s) of the symbol's definition. With multiple
    definitions (overloads, templates), every location is returned.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.
    line:
        1-based line number of the cursor position.
    character:
        0-based character offset within the line.

    Returns
    -------
    str
        JSON array of ``{"path", "line", "character"}`` objects (line is
        1-based), or ``"Error: ..."`` on failure.

    Examples
    --------
    clangd_definition("src/main.cpp", 10, 5)
    """
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        client = await _get_client()
        result = await client.definition(resolved, lsp_line, lsp_char)
        return _truncate_payload(_format_locations(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_references(
    path: str,
    line: int,
    character: int,
    include_declaration: bool = True,
) -> str:
    """Find all references to the symbol at the given cursor position.

    Backed by ``clangd`` (``textDocument/references``). Both read and
    write uses are returned; declarations can be excluded via
    ``include_declaration=False``.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.
    line:
        1-based line number of the cursor position.
    character:
        0-based character offset within the line.
    include_declaration:
        When ``True`` (default), the symbol's declaration site is
        included in the result set.

    Returns
    -------
    str
        JSON array of ``{"path", "line", "character"}`` objects
        (line is 1-based), or ``"Error: ..."`` on failure.

    Examples
    --------
    clangd_references("src/main.cpp", 10, 5)
    clangd_references("src/main.cpp", 10, 5, include_declaration=False)
    """
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        client = await _get_client()
        result = await client.references(
            resolved, lsp_line, lsp_char, include_declaration=include_declaration
        )
        return _truncate_payload(_format_locations(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_document_symbols(path: str) -> str:
    """List symbols defined in a single C/C++ source file.

    Backed by ``clangd`` (``textDocument/documentSymbol``). Returns a
    flat list with hierarchical depth so the LLM does not need to
    recurse: each entry carries a ``depth`` field (root = 0) and
    ``path``/``line``/``character`` (1-based line) of its declaration.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.

    Returns
    -------
    str
        JSON array of symbol objects, or ``"Error: ..."`` on failure.

    Examples
    --------
    clangd_document_symbols("src/main.cpp")
    """
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client = await _get_client()
        result = await client.document_symbol(resolved)
        return _truncate_payload(_format_hierarchical_symbols(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_workspace_symbols(query: str) -> str:
    """Search for symbols across the entire workspace.

    Backed by ``clangd`` (``workspace/symbol``). Performs a fuzzy
    search by symbol name. The workspace must be indexed for accurate
    results — a ``compile_commands.json`` at the project root is
    strongly recommended.

    Parameters
    ----------
    query:
        Search string (matches symbol name). Empty string returns all
        indexed symbols (may be slow on large projects).

    Returns
    -------
    str
        JSON array of ``{"name", "kind", "container_name", "path",
        "line", "character"}`` objects (line is 1-based), or
        ``"Error: ..."`` on failure.

    Examples
    --------
    clangd_workspace_symbols("Greeter")
    """
    try:
        client = await _get_client()
        result = await client.workspace_symbol(query)
        return _truncate_payload(_format_symbols(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_rename(
    path: str, line: int, character: int, new_name: str
) -> str:
    """Return a ``WorkspaceEdit`` that renames the symbol at the cursor.

    Backed by ``clangd`` (``textDocument/rename``). The edit is *not*
    applied automatically — the LLM is expected to apply it via
    ``write_file`` / ``replace_in_file``. The edit covers every
    reference and declaration of the symbol in the workspace.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.
    line:
        1-based line number of the cursor position.
    character:
        0-based character offset within the line.
    new_name:
        The new name to give the symbol. Must be a valid C++ identifier.

    Returns
    -------
    str
        JSON object with a ``"changes"`` key (mapping file path to a
        list of ``{"range", "new_text"}`` edits), or ``"Error: ..."``
        on failure. If the symbol cannot be renamed, returns ``null``.

    Examples
    --------
    clangd_rename("src/main.cpp", 10, 5, "new_name")
    """
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        client = await _get_client()
        result = await client.rename(resolved, lsp_line, lsp_char, new_name)
        if result is None:
            return json.dumps({"changes": {}})
        return _truncate_payload(result)
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_type_hierarchy(
    path: str,
    line: int,
    character: int,
    direction: str = "subtypes",
) -> str:
    """Explore the type hierarchy at the given cursor position.

    Backed by ``clangd`` (``textDocument/prepareTypeHierarchy`` plus
    ``typeHierarchy/subtypes`` and ``typeHierarchy/supertypes``). The
    type at the cursor is the hierarchy root.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.
    line:
        1-based line number of the cursor position.
    character:
        0-based character offset within the line.
    direction:
        One of:

        - ``"subtypes"``  (default) — classes that inherit from the type.
        - ``"supertypes"``           — base classes of the type.
        - ``"both"``                 — both directions in one call.

    Returns
    -------
    str
        JSON object ``{"item": {...}, "supertypes": [...],
        "subtypes": [...]}`` with 1-based line numbers, or
        ``"Error: ..."`` on failure.

    Examples
    --------
    clangd_type_hierarchy("src/main.cpp", 10, 5)
    clangd_type_hierarchy("src/main.cpp", 10, 5, direction="both")
    """
    try:
        if direction not in _VALID_TYPE_HIERARCHY_DIRS:
            return (
                f"Error: direction must be one of {_VALID_TYPE_HIERARCHY_DIRS}, "
                f"got {direction!r}"
            )
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        client = await _get_client()
        items = await client.prepare_type_hierarchy(resolved, lsp_line, lsp_char)
        if not items:
            return json.dumps({"item": None, "supertypes": [], "subtypes": []})

        root = items[0]
        supertypes: list[dict[str, Any]] = []
        subtypes: list[dict[str, Any]] = []
        if direction in ("supertypes", "both"):
            supertypes = _format_type_hierarchy_items(
                await client.type_hierarchy_supertypes(root)
            )
        if direction in ("subtypes", "both"):
            subtypes = _format_type_hierarchy_items(
                await client.type_hierarchy_subtypes(root)
            )
        root_dict = cast(dict[str, Any], root)
        return _truncate_payload(
            {
                "item": _format_type_hierarchy_items([root_dict])[0],
                "supertypes": supertypes,
                "subtypes": subtypes,
            }
        )
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def clangd_call_hierarchy(
    path: str,
    line: int,
    character: int,
    direction: str = "outgoing",
) -> str:
    """Explore the call hierarchy at the given cursor position.

    Backed by ``clangd`` (``textDocument/prepareCallHierarchy`` plus
    ``callHierarchy/incomingCalls`` and ``callHierarchy/outgoingCalls``).
    The function at the cursor is the hierarchy root.

    Parameters
    ----------
    path:
        Path to the C or C++ source file. Must be inside the project root.
    line:
        1-based line number of the cursor position.
    character:
        0-based character offset within the line.
    direction:
        One of:

        - ``"outgoing"``  (default) — functions called by the root.
        - ``"incoming"``            — functions that call the root.
        - ``"both"``                — both directions in one call.

    Returns
    -------
    str
        JSON object ``{"item": {...}, "outgoing": [...],
        "incoming": [...]}`` with 1-based line numbers, or
        ``"Error: ..."`` on failure.

    Examples
    --------
    clangd_call_hierarchy("src/main.cpp", 10, 5)
    clangd_call_hierarchy("src/main.cpp", 10, 5, direction="both")
    """
    try:
        if direction not in _VALID_CALL_HIERARCHY_DIRS:
            return (
                f"Error: direction must be one of {_VALID_CALL_HIERARCHY_DIRS}, "
                f"got {direction!r}"
            )
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        client = await _get_client()
        items = await client.prepare_call_hierarchy(resolved, lsp_line, lsp_char)
        if not items:
            return json.dumps({"item": None, "outgoing": [], "incoming": []})

        root = items[0]
        root_item: dict[str, Any] = {
            "name": root.get("name", ""),
            "kind": _symbol_kind_name(root.get("kind")),
            "detail": root.get("detail", ""),
            "path": _uri_to_path_safe(root.get("uri", "")),
        }
        rng = root.get("selectionRange") or root.get("range") or {}
        start = rng.get("start") or {}
        root_item["line"] = int(start.get("line", 0)) + 1
        root_item["character"] = int(start.get("character", 0))

        outgoing: list[dict[str, Any]] = []
        incoming: list[dict[str, Any]] = []
        if direction in ("outgoing", "both"):
            outgoing = _format_call_edges(
                await client.call_hierarchy_outgoing(root), direction="outgoing"
            )
        if direction in ("incoming", "both"):
            incoming = _format_call_edges(
                await client.call_hierarchy_incoming(root), direction="incoming"
            )
        return _truncate_payload(
            {"item": root_item, "outgoing": outgoing, "incoming": incoming}
        )
    except Exception as exc:
        return f"Error: {exc}"

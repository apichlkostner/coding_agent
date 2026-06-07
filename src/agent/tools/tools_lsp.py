"""Generic LSP tools for the coding agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from agent.lsp import ServerSpec, WorkspaceContext, get_client_manager
from agent.tools.tools_filesystem import _is_subpath

OUTPUT_CHAR_LIMIT = 8_000

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
_DIAGNOSTIC_SEVERITIES = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}


def _resolve_path(path: str) -> str | None:
    if not _is_subpath(path, strict=True):
        return None
    return str(Path(path).resolve())


async def _resolve_client(
    path: str,
    *,
    language: str = "",
) -> tuple[Any, ServerSpec, WorkspaceContext]:
    manager = await get_client_manager()
    return await manager.get_client_for_path(path, language=language)


def _line_col_to_0based(line: int, character: int) -> tuple[int, int]:
    return max(0, int(line) - 1), max(0, int(character))


def _symbol_kind_name(kind: Any) -> str:
    if isinstance(kind, int):
        return _SYMBOL_KIND_NAMES.get(kind, str(kind))
    return str(kind)


def _uri_to_path_safe(uri: str) -> str:
    if not uri:
        return ""
    if not uri.startswith("file://"):
        return uri
    try:
        from agent.lsp import uri_to_path

        return uri_to_path(uri)
    except Exception:
        return uri


def _truncate_payload(obj: Any) -> str:
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


def _format_locations(locations: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for loc in locations or []:
        rng = loc.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                "path": _uri_to_path_safe(loc.get("uri", "")),
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
            }
        )
    return out


def _format_document_symbols(symbols: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        rng = node.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                "name": node.get("name", ""),
                "kind": _symbol_kind_name(node.get("kind")),
                "detail": node.get("detail", ""),
                "depth": depth,
                "path": _uri_to_path_safe(node.get("uri", "")),
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
            }
        )
        for child in node.get("children") or []:
            walk(child, depth + 1)

    for symbol in symbols or []:
        walk(symbol, 0)
    return out


def _format_workspace_symbols(symbols: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sym in symbols or []:
        location = sym.get("location") or {}
        rng = location.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                "name": sym.get("name", ""),
                "kind": _symbol_kind_name(sym.get("kind")),
                "container_name": sym.get("containerName", ""),
                "path": _uri_to_path_safe(location.get("uri", "")),
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
            }
        )
    return out


def _format_diagnostics(path: str, diagnostics: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for diagnostic in diagnostics or []:
        rng = diagnostic.get("range") or {}
        start = rng.get("start") or {}
        out.append(
            {
                "path": path,
                "line": int(start.get("line", 0)) + 1,
                "character": int(start.get("character", 0)),
                "severity": _DIAGNOSTIC_SEVERITIES.get(
                    int(diagnostic.get("severity", 3)), "information"
                ),
                "code": diagnostic.get("code"),
                "source": diagnostic.get("source", ""),
                "message": diagnostic.get("message", ""),
            }
        )
    return out


def _capability_supported(capabilities: dict[str, Any], capability: str) -> bool:
    value = capabilities.get(capability)
    if isinstance(value, bool):
        return value
    return value is not None


def _unsupported_feature_error(spec: ServerSpec, label: str) -> str:
    return f"Error: {label} are not supported by {spec.server_id}"


@tool
async def lsp_definition(path: str, line: int, character: int) -> str:
    """Return definition locations for the symbol at the given cursor."""
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client, spec, _context = await _resolve_client(resolved)
        if not _capability_supported(
            client.server_capabilities(), "definitionProvider"
        ):
            return _unsupported_feature_error(spec, "definitions")
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        result = await client.definition(resolved, lsp_line, lsp_char)
        return _truncate_payload(_format_locations(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def lsp_references(
    path: str,
    line: int,
    character: int,
    include_declaration: bool = True,
) -> str:
    """Return references for the symbol at the given cursor."""
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client, spec, _context = await _resolve_client(resolved)
        if not _capability_supported(
            client.server_capabilities(), "referencesProvider"
        ):
            return _unsupported_feature_error(spec, "references")
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        result = await client.references(
            resolved, lsp_line, lsp_char, include_declaration=include_declaration
        )
        return _truncate_payload(_format_locations(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def lsp_document_symbols(path: str) -> str:
    """Return document symbols for a single file."""
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client, spec, _context = await _resolve_client(resolved)
        if not _capability_supported(
            client.server_capabilities(), "documentSymbolProvider"
        ):
            return _unsupported_feature_error(spec, "document symbols")
        result = await client.document_symbol(resolved)
        return _truncate_payload(_format_document_symbols(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def lsp_workspace_symbols(path: str, query: str, language: str = "") -> str:
    """Return workspace symbols anchored to the project implied by path."""
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client, spec, _context = await _resolve_client(resolved, language=language)
        if not _capability_supported(
            client.server_capabilities(), "workspaceSymbolProvider"
        ):
            return _unsupported_feature_error(spec, "workspace symbols")
        await client.sync_document(resolved)
        result = await client.workspace_symbol(query)
        return _truncate_payload(_format_workspace_symbols(result))
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def lsp_rename(path: str, line: int, character: int, new_name: str) -> str:
    """Return a WorkspaceEdit for renaming the symbol at the given cursor."""
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client, spec, _context = await _resolve_client(resolved)
        if not _capability_supported(client.server_capabilities(), "renameProvider"):
            return _unsupported_feature_error(spec, "rename")
        lsp_line, lsp_char = _line_col_to_0based(line, character)
        result = await client.rename(resolved, lsp_line, lsp_char, new_name)
        return _truncate_payload(result or {"changes": {}})
    except Exception as exc:
        return f"Error: {exc}"


@tool
async def lsp_diagnostics(path: str) -> str:
    """Return diagnostics published for the given file."""
    try:
        resolved = _resolve_path(path)
        if resolved is None:
            return "Error: Path is not inside the project folder"
        client, _spec, _context = await _resolve_client(resolved)
        generation = client.diagnostics_generation(resolved)
        await client.sync_document(resolved)
        diagnostics = client.get_diagnostics(resolved)
        if not diagnostics:
            try:
                diagnostics = await client.await_diagnostics(
                    resolved, timeout=1.0, after_generation=generation
                )
            except TimeoutError:
                diagnostics = client.get_diagnostics(resolved)
        return _truncate_payload(_format_diagnostics(resolved, diagnostics))
    except Exception as exc:
        return f"Error: {exc}"

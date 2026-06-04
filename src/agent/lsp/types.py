"""Minimal LSP data types used by the clangd client.

These are plain :class:`typing.TypedDict` definitions — strict enough to
catch obvious mistakes with mypy, loose enough to accept the partial
responses clangd actually returns.  All dicts use ``total=False`` so a
missing field is treated as ``NotRequired`` rather than a type error.

Only the subsets of LSP that the seven exposed tools touch are defined
here.  Adding a new tool rarely requires a new type — most parameters
and results fall out of these existing shapes.
"""

from __future__ import annotations

from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Positions, ranges, locations
# ---------------------------------------------------------------------------


class Position(TypedDict, total=False):
    """A 0-based line / character position."""

    line: int
    character: int


class Range(TypedDict, total=False):
    """A range between two positions, end-exclusive."""

    start: Position
    end: Position


class Location(TypedDict, total=False):
    """A source location: a document URI and a range within it."""

    uri: str
    range: Range


# ---------------------------------------------------------------------------
# Text document identifiers
# ---------------------------------------------------------------------------


class TextDocumentIdentifier(TypedDict, total=False):
    """Identifies a text document by its URI."""

    uri: str


class VersionedTextDocumentIdentifier(TypedDict, total=False):
    """A text document identifier with a monotonic version number."""

    uri: str
    version: int


class TextDocumentItem(TypedDict, total=False):
    """An item to open in the server (``didOpen``)."""

    uri: str
    languageId: str
    version: int
    text: str


class TextDocumentContentChangeEvent(TypedDict, total=False):
    """A change to a text document.  Whole-document replacement is used."""

    text: str


class TextDocumentPositionParams(TypedDict, total=False):
    """A position inside a text document (used by many LSP requests)."""

    textDocument: TextDocumentIdentifier
    position: Position


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


class DocumentSymbol(TypedDict, total=False):
    """A hierarchical symbol from ``textDocument/documentSymbol``.

    The recursive shape is approximated with ``children``; the LSP spec
    also permits a flat ``SymbolInformation`` array, which clangd does
    not use for ``documentSymbol``.
    """

    name: str
    kind: int
    range: Range
    selectionRange: Range
    detail: str
    children: list[DocumentSymbol]


class WorkspaceSymbol(TypedDict, total=False):
    """A flat symbol from ``workspace/symbol``."""

    name: str
    kind: int
    location: Location
    containerName: str


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


class CompletionItem(TypedDict, total=False):
    """A single completion entry from ``textDocument/completion``."""

    label: str
    kind: int
    detail: str
    documentation: Any
    sortText: str
    insertText: str
    insertTextFormat: int


class CompletionList(TypedDict, total=False):
    """The response of ``textDocument/completion``.

    Some servers return a bare ``list[CompletionItem]`` instead of this
    wrapper — the client normalises both into a :class:`CompletionList`.
    """

    isIncomplete: bool
    items: list[CompletionItem]


# ---------------------------------------------------------------------------
# Hierarchies
# ---------------------------------------------------------------------------


class CallHierarchyItem(TypedDict, total=False):
    """An item in a call hierarchy."""

    name: str
    kind: int
    detail: str
    uri: str
    range: Range
    selectionRange: Range


class CallHierarchyIncomingCall(TypedDict, total=False):
    """An incoming call edge in a call hierarchy."""

    from_: CallHierarchyItem  # accessed via raw key "from" — see client
    fromRanges: list[Range]


class CallHierarchyOutgoingCall(TypedDict, total=False):
    """An outgoing call edge in a call hierarchy."""

    to: CallHierarchyItem
    fromRanges: list[Range]


class TypeHierarchyItem(TypedDict, total=False):
    """An item in a type hierarchy."""

    name: str
    kind: int
    detail: str
    uri: str
    range: Range
    selectionRange: Range


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------


class TextEdit(TypedDict, total=False):
    """A single text edit inside a document."""

    range: Range
    newText: str


class WorkspaceEdit(TypedDict, total=False):
    """A batch of edits to apply across documents.

    The full LSP spec allows per-resource edit arrays; clangd returns the
    simple ``changes`` form.
    """

    changes: dict[str, list[TextEdit]]


# ---------------------------------------------------------------------------
# Server capabilities (we only read a tiny subset)
# ---------------------------------------------------------------------------


class ServerCapabilities(TypedDict, total=False):
    """Subset of ``initialize`` result capabilities we actually inspect."""

    definitionProvider: bool
    referencesProvider: bool
    renameProvider: Any
    completionProvider: Any
    documentSymbolProvider: bool
    workspaceSymbolProvider: bool
    typeHierarchyProvider: bool
    callHierarchyProvider: bool


class InitializeResult(TypedDict, total=False):
    """The full result of an ``initialize`` request."""

    capabilities: ServerCapabilities
    serverInfo: dict[str, Any]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


class Diagnostic(TypedDict, total=False):
    """A single diagnostic from ``textDocument/publishDiagnostics``."""

    range: Range
    severity: int
    code: Any
    source: str
    message: str
    relatedInformation: list[Any]


__all__ = [
    "CallHierarchyIncomingCall",
    "CallHierarchyItem",
    "CallHierarchyOutgoingCall",
    "CompletionItem",
    "CompletionList",
    "Diagnostic",
    "DocumentSymbol",
    "InitializeResult",
    "Location",
    "Position",
    "Range",
    "ServerCapabilities",
    "TextDocumentContentChangeEvent",
    "TextDocumentIdentifier",
    "TextDocumentItem",
    "TextDocumentPositionParams",
    "TextEdit",
    "TypeHierarchyItem",
    "VersionedTextDocumentIdentifier",
    "WorkspaceEdit",
    "WorkspaceSymbol",
]

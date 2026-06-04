"""LSP client package.

Public API
----------
- :class:`ClangdClient` — async client that drives a ``clangd`` subprocess.
- :func:`get_default_client` — process-wide singleton accessor (lazy start).
- :func:`reset_default_client` — stop and clear the singleton.
- :func:`path_to_uri` / :func:`uri_to_path` — path/URI conversions.
- :class:`LSPError` — error returned by the language server.
- Typed dicts from :mod:`agent.lsp.types`.
"""

from agent.lsp.client import (
    ClangdClient,
    LSPError,
    get_default_client,
    path_to_uri,
    reset_default_client,
    uri_to_path,
)
from agent.lsp.framing import LSPProtocolError, read_message, write_message
from agent.lsp.types import (
    CallHierarchyIncomingCall,
    CallHierarchyItem,
    CallHierarchyOutgoingCall,
    CompletionItem,
    CompletionList,
    Diagnostic,
    DocumentSymbol,
    InitializeResult,
    Location,
    Position,
    Range,
    ServerCapabilities,
    TextDocumentContentChangeEvent,
    TextDocumentIdentifier,
    TextDocumentItem,
    TextDocumentPositionParams,
    TextEdit,
    TypeHierarchyItem,
    VersionedTextDocumentIdentifier,
    WorkspaceEdit,
    WorkspaceSymbol,
)

__all__ = [
    "CallHierarchyIncomingCall",
    "CallHierarchyItem",
    "CallHierarchyOutgoingCall",
    "ClangdClient",
    "CompletionItem",
    "CompletionList",
    "Diagnostic",
    "DocumentSymbol",
    "InitializeResult",
    "LSPError",
    "LSPProtocolError",
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
    "get_default_client",
    "path_to_uri",
    "read_message",
    "reset_default_client",
    "uri_to_path",
    "write_message",
]

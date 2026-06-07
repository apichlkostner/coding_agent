"""LSP client package.

Public API
----------
- :class:`LanguageServerClient` — async client that drives a language-server subprocess.
- :class:`ClangdClient` — backward-compatible alias for the default C/C++ client type.
- :func:`get_default_client` — process-wide singleton accessor (lazy start).
- :func:`reset_default_client` — stop and clear the singleton.
- :func:`path_to_uri` / :func:`uri_to_path` — path/URI conversions.
- :class:`LSPError` — error returned by the language server.
- Typed dicts from :mod:`agent.lsp.types`.
"""

from agent.lsp.client import (
    ClangdClient,
    LanguageServerClient,
    LSPError,
    get_default_client,
    path_to_uri,
    reset_default_client,
    uri_to_path,
)
from agent.lsp.framing import LSPProtocolError, read_message, write_message
from agent.lsp.registry import (
    LanguageServerClientManager,
    ServerSpec,
    WorkspaceContext,
    detect_workspace_context,
    get_client_manager,
    get_server_spec_for_path,
    reset_client_manager,
)
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
    "LanguageServerClient",
    "CompletionItem",
    "CompletionList",
    "Diagnostic",
    "DocumentSymbol",
    "InitializeResult",
    "LSPError",
    "LSPProtocolError",
    "LanguageServerClientManager",
    "Location",
    "Position",
    "Range",
    "ServerCapabilities",
    "ServerSpec",
    "TextDocumentContentChangeEvent",
    "TextDocumentIdentifier",
    "TextDocumentItem",
    "TextDocumentPositionParams",
    "TextEdit",
    "TypeHierarchyItem",
    "VersionedTextDocumentIdentifier",
    "WorkspaceEdit",
    "WorkspaceContext",
    "WorkspaceSymbol",
    "detect_workspace_context",
    "get_client_manager",
    "get_server_spec_for_path",
    "get_default_client",
    "path_to_uri",
    "read_message",
    "reset_client_manager",
    "reset_default_client",
    "uri_to_path",
    "write_message",
]

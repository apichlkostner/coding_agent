# Plan: Matrix Adapter

## Summary

Add a Matrix messenger adapter to `src/agent/adapters/` that connects the existing
`MessageRouter`/`AgentService` pipeline to a Matrix homeserver via `matrix-nio`.
The bot joins rooms manually (no auto-accept of invitations), responds to every
`m.room.message` text event in joined rooms (except its own), and threads replies
using `m.in_reply_to`. Authentication is via a pre-issued access token. One small
cross-cutting change is required: `AgentService._make()` is extended to forward
`InboundMessage.metadata` into every `OutboundMessage`, allowing `MatrixAdapter.send()`
to read the original `event_id` for correct reply threading without any adapter-side
state.

## Assumptions

- Python `matrix-nio` package (PyPI: `matrix-nio`) provides `nio.AsyncClient`, async
  callbacks, and `room_send()`. No E2E encryption is required.
- The Matrix bot account is pre-created on the homeserver and the access token is
  obtained out-of-band (e.g. via `curl` against `/_matrix/client/v3/login` or
  Element's dev-tools session export).
- Room membership is managed externally; the adapter does not auto-join on invite.
- `thread_id = "matrix-{room_id}-{sender_id}"` — one LangGraph conversation per
  user per room, matching Discord's per-user-per-channel model.
- Only `m.room.message` events with `msgtype: m.text` are dispatched to the agent.
  Reactions, files, edits, and state events are ignored.
- Only `response` and `error` outbound message types are delivered to Matrix.
  Intermediate `tool_call` / `tool_result` events are silently dropped.
- Responses are sent as plain text with Matrix reply threading (`m.in_reply_to`).
  No HTML formatting.
- Matrix has no strict per-message character limit comparable to Discord's 2000-char
  cap; no chunking is implemented in v1.
- The sync loop calls `client.sync()` in a manual while-loop (not `sync_forever()`)
  for explicit backoff control. The `next_batch` token is tracked across iterations
  so only new events are processed (no history replay on reconnect).
- Reconnect uses exponential backoff with jitter, capped at 60 seconds.
- `room_send()` returns `nio.RoomSendResponse` on success or `nio.ErrorResponse` on
  failure; failures are logged and dropped without raising.
- The adapter is enabled when all three env vars are set:
  `MATRIX_HOMESERVER_URL`, `MATRIX_ACCESS_TOKEN`, `MATRIX_USER_ID`.
  If any is missing and `"matrix"` is in `ENABLED_ADAPTERS`, a warning is logged
  and the adapter is skipped (same pattern as `DiscordAdapter`).
- Default `ENABLED_ADAPTERS` remains `terminal,discord,heartbeat`; Matrix is opt-in
  and must be explicitly added.

---

## Steps

### 1. Add `matrix-nio` dependency

- **File:** `pyproject.toml`
- **Changes:** Add `"matrix-nio>=0.21"` to the `dependencies` list.
- **Verification:** `uv sync` completes without error; `python -c "import nio"` succeeds.

---

### 2. Extend `AgentService` to forward inbound metadata

- **File:** `src/agent/router/agent_service.py`
- **Changes:** In `run()`, change the inner `_make()` closure to merge
  `InboundMessage.metadata` into the outbound metadata dict, with `msg_type`
  and `node_name` taking precedence:
  ```python
  def _make(content: str, msg_type: str, node_name: str = "") -> OutboundMessage:
      meta = {**message.metadata, "msg_type": msg_type, "node_name": node_name}
      return OutboundMessage(
          adapter_id=message.adapter_id,
          reply_channel_id=message.reply_channel_id,
          content=content,
          metadata=meta,
      )
  ```
  This means any key placed in `InboundMessage.metadata` (e.g. `event_id`,
  `room_name`) is available on every corresponding `OutboundMessage`.
  Existing adapters are unaffected: they only read `msg_type` and `node_name`,
  which continue to be set correctly.
- **Verification:**
  - All existing router and adapter tests pass unchanged.
  - Add a targeted unit test to `tests/test_router.py` asserting that
    `AgentService` forwards an arbitrary inbound metadata key to all yielded
    outbound messages.

---

### 3. Add Matrix configuration to `config.py`

- **File:** `src/agent/config.py`
- **Changes:**
  - Add a `MatrixSettings` frozen dataclass:
    ```python
    @dataclass(frozen=True)
    class MatrixSettings:
        homeserver_url: str = field(default="")
        access_token: str = field(default="")
        user_id: str = field(default="")
    ```
  - Add a `matrix: MatrixSettings` field to `Settings` with
    `default_factory=MatrixSettings`.
  - In `get_settings()`, parse:
    ```
    MATRIX_HOMESERVER_URL  → matrix.homeserver_url
    MATRIX_ACCESS_TOKEN    → matrix.access_token
    MATRIX_USER_ID         → matrix.user_id
    ```
- **Verification:** Existing config tests pass unchanged. New unit tests assert
  that all three fields are read from env and default to empty strings.

---

### 4. Add Matrix env vars to `.env.example`

- **File:** `.env.example`
- **Changes:** Add a `# ─── Matrix Bot ──` section:
  ```
  # Create a bot account on your homeserver. Obtain an access token via:
  #   curl -XPOST 'https://<homeserver>/_matrix/client/v3/login' \
  #        -d '{"type":"m.login.password","user":"<user>","password":"<pass>"}'
  # Then add the bot to rooms manually via an admin account or Element.
  # Add "matrix" to ENABLED_ADAPTERS to activate.
  MATRIX_HOMESERVER_URL=https://matrix.org
  MATRIX_ACCESS_TOKEN=
  MATRIX_USER_ID=@bot:matrix.org
  ```
- **Verification:** Visual inspection.

---

### 5. Implement `MatrixAdapter`

- **File:** `src/agent/adapters/matrix_adapter.py`
- **Changes:** Implement `MatrixAdapter(BaseAdapter)` as follows.

  **Class-level:**
  ```python
  adapter_id = "matrix"
  ```

  **`__init__(settings: MatrixSettings) -> None`**
  - Store `settings`.
  - Create `self._client = nio.AsyncClient(settings.homeserver_url, settings.user_id)`.
  - Set `self._client.access_token = settings.access_token`.
  - `self._router: MessageRouter | None = None`.

  **`async start(router: MessageRouter) -> None`**
  - Store `self._router = router`.
  - Register a callback for `nio.RoomMessageText`:
    ```python
    async def _on_message(room: nio.MatrixRoom, event: nio.RoomMessageText) -> None:
        if event.sender == self._settings.user_id:
            return  # ignore own messages — loop prevention
        inbound = InboundMessage(
            adapter_id=self.adapter_id,
            thread_id=f"matrix-{room.room_id}-{event.sender}",
            content=event.body,
            reply_channel_id=room.room_id,
            user_id=event.sender,
            metadata={
                "event_id": event.event_id,
                "room_name": room.display_name or room.room_id,
            },
        )
        await router.dispatch(inbound)  # fire-and-forget
    self._client.add_event_callback(_on_message, nio.RoomMessageText)
    ```
  - Run the sync loop:
    ```python
    attempt = 0
    next_batch: str | None = None
    while True:
        try:
            response = await self._client.sync(
                timeout=30_000,
                full_state=False,
                since=next_batch,
            )
            if isinstance(response, nio.SyncResponse):
                next_batch = response.next_batch
                attempt = 0
            else:
                raise RuntimeError(f"Unexpected sync response: {response}")
        except Exception as exc:
            backoff = min(2 ** attempt + random.uniform(0, 1), 60.0)
            logger.error(
                "MatrixAdapter sync error (retry in %.1fs): %s", backoff, exc
            )
            await asyncio.sleep(backoff)
            attempt += 1
    ```

  **`async send(message: OutboundMessage) -> None`**
  - Return immediately if `message.msg_type not in ("response", "error")`.
  - Return immediately if `message.content` is empty.
  - Build event content:
    ```python
    content: dict[str, object] = {
        "msgtype": "m.text",
        "body": message.content,
    }
    event_id: str | None = message.metadata.get("event_id")
    if event_id:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": event_id}}
    ```
  - Send:
    ```python
    response = await self._client.room_send(
        room_id=message.reply_channel_id,
        message_type="m.room.message",
        content=content,
    )
    if isinstance(response, nio.ErrorResponse):
        logger.error(
            "MatrixAdapter: room_send failed for room %s: %s",
            message.reply_channel_id,
            response,
        )
    ```

- **Verification:** Unit tests pass (Step 7). Manual smoke test against a real
  homeserver.

---

### 6. Export `MatrixAdapter` from the adapters package

- **File:** `src/agent/adapters/__init__.py`
- **Changes:** Add:
  ```python
  from agent.adapters.matrix_adapter import MatrixAdapter
  ```
  and include `"MatrixAdapter"` in `__all__`.
- **Verification:** `from agent.adapters import MatrixAdapter` succeeds in a
  Python shell.

---

### 7. Register `MatrixAdapter` in `build_router()`

- **File:** `src/agent/__main__.py`
- **Changes:**
  - Import `MatrixAdapter` from `agent.adapters`.
  - After the `heartbeat` block, add:
    ```python
    if "matrix" in enabled:
        ms = settings.matrix
        if ms.homeserver_url and ms.access_token and ms.user_id:
            router.register(MatrixAdapter(ms))
            logger.info("Registered MatrixAdapter.")
        else:
            logger.warning(
                "Matrix adapter is enabled but credentials are incomplete "
                "(MATRIX_HOMESERVER_URL / MATRIX_ACCESS_TOKEN / MATRIX_USER_ID)"
                " — skipping."
            )
    ```
- **Verification:** New `TestBuildRouter` tests confirm adapter is registered when
  credentials are present and skipped (with a logged warning) when any is missing.

---

### 8. Write tests

- **File:** `tests/test_router.py`
- **Changes:** Add a test to `TestAgentService` asserting that inbound metadata
  keys are forwarded to all yielded outbound messages:
  ```python
  async def test_inbound_metadata_forwarded_to_outbound(self) -> None:
      graph = _make_graph({"agent": {"messages": [AIMessage(content="ok")]}})
      service = AgentService(graph)
      inbound = _inbound()
      inbound = dataclasses.replace(inbound, metadata={"event_id": "evt-42"})
      results = [msg async for msg in service.run(inbound)]
      assert results[0].metadata["event_id"] == "evt-42"
  ```

- **File:** `tests/test_adapters.py`
- **Changes:** Add `TestMatrixAdapterSend` and `TestMatrixAdapterStart`.

  **`TestMatrixAdapterSend`**
  - `send()` with `msg_type="response"` calls `client.room_send` with correct
    `room_id` and `body`.
  - `send()` with `msg_type="error"` calls `client.room_send`.
  - `send()` with `msg_type="tool_call"` does **not** call `client.room_send`.
  - `send()` with `msg_type="tool_result"` does **not** call `client.room_send`.
  - `send()` with empty content does not call `client.room_send`.
  - `send()` includes `m.relates_to` / `m.in_reply_to` when `metadata["event_id"]`
    is present.
  - `send()` omits `m.relates_to` when `event_id` is absent from metadata.
  - `send()` logs an error and does not raise when `room_send` returns
    `nio.ErrorResponse`.

  **`TestMatrixAdapterStart`**
  - Message with `event.sender == bot_user_id` is not dispatched.
  - Valid text message builds `InboundMessage` with correct `adapter_id`,
    `thread_id` (`matrix-{room_id}-{sender_id}`), `content`, `reply_channel_id`,
    `user_id`, and `metadata["event_id"]`.
  - Sync error triggers backoff: mock `client.sync` to raise twice then succeed;
    assert `asyncio.sleep` called with increasing delays.
  - `next_batch` token from `SyncResponse` is passed as `since` on the next
    `sync()` call (no history replay).

- **File:** `tests/test_main.py`
- **Changes:** Extend `TestBuildRouter`:
  - Matrix adapter registered when all three credentials present and
    `"matrix"` is in `enabled_adapters`.
  - Matrix adapter skipped with warning when any credential is empty.

- **Verification:** `uv run pytest` passes with no regressions.

---

### 9. Update `README.md`

- **File:** `README.md`
- **Changes:**
  - Add `matrix` to the `ENABLED_ADAPTERS` description in the configuration table.
  - Add `MATRIX_HOMESERVER_URL`, `MATRIX_ACCESS_TOKEN`, `MATRIX_USER_ID` rows to
    the configuration table.
  - Add a `### Matrix` section under `## Adapter Setup`:
    1. How to create a bot account on a homeserver.
    2. How to obtain an access token via `/_matrix/client/v3/login`.
    3. How to manually add the bot to a room (via Element or admin API).
    4. Note that `"matrix"` must be added to `ENABLED_ADAPTERS` explicitly.
    5. Thread ID scheme: `matrix-{room_id}-{sender_id}`.
  - Update the architecture diagram to include `MatrixAdapter`.
- **Verification:** Visual inspection.

---

## Open Questions

None. All design decisions have been resolved:

| # | Topic | Resolution |
|---|-------|-----------|
| OQ-1 | `event_id` for reply threading | Forward `InboundMessage.metadata` through `AgentService._make()` (Step 2). No adapter-side cache needed; no race condition. |
| OQ-2 | `nio.sync()` history replay | Pass `full_state=False` and track `next_batch` across iterations (Step 5). First call with `since=None` fetches current state only; subsequent calls pass the token. |
| OQ-3 | `room_send()` error handling | Check `isinstance(response, nio.ErrorResponse)`; log and return without raising (Step 5). |
| OQ-4 | Default `ENABLED_ADAPTERS` | Matrix is opt-in; default remains `terminal,discord,heartbeat`. Users must explicitly add `matrix` (Step 7, Step 9). |

---

## Out of Scope

- End-to-end encrypted rooms (E2E / Olm/Megolm).
- Auto-accepting room invitations.
- Mention-only or prefix (`!agent`) trigger mode.
- Per-room enable/disable configuration.
- Message chunking (Matrix's per-message size limit is much higher than Discord's).
- Handling of edited or redacted messages.
- Typing indicator in Matrix rooms.
- Reaction or Matrix Threads (MSC3440) support.
- Multi-homeserver support.
- Device verification / cross-signing.
- Any other messenger (Telegram, Slack, etc.).

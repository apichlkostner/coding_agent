# Plan: Add Ollama Local LLM Provider Support

## Summary
Add a third LLM provider option (`ollama`) so the agent can run against locally hosted models (for example via Ollama) in addition to OpenAI and Anthropic. The implementation will extend runtime configuration, model factory logic, dependency setup, documentation, and tests so users can select a local model through environment variables without changing application code.

## Assumptions
- Scope is limited to adding Ollama as an additional chat provider, not replacing existing providers.
- Ollama is expected to be running separately (local daemon/container) and reachable over HTTP.
- Local models are selected through existing `MODEL_NAME` semantics, with a provider-specific default when unset.
- No new adapter or graph behavior is required; provider selection remains centralized in `src/agent/config.py`.
- Tool-calling behavior depends on chosen local model; this plan does not guarantee every Ollama model supports tool calling equally well.

## Steps
### 1. Add Ollama runtime dependency
- File(s): `pyproject.toml`
- Changes: Add `langchain-ollama` to project dependencies so `ChatOllama` can be instantiated in production code.
- Verification: Run `uv sync` successfully and confirm the package resolves; run `uv run python -c "import langchain_ollama"`.

### 2. Extend provider config schema for local models
- File(s): `src/agent/config.py`
- Changes: Expand provider type and defaults to include `ollama`; add any required Ollama-specific settings (for example `OLLAMA_BASE_URL`) to `Settings`; update env parsing/validation and provider error messages to include the new option.
- Verification: Unit tests for `get_settings()` pass, including `LLM_PROVIDER=ollama`, default model resolution, and invalid-provider validation text.

### 3. Implement Ollama branch in LLM factory
- File(s): `src/agent/config.py`
- Changes: Update `get_llm()` to instantiate `ChatOllama` when `llm_provider == "ollama"`, passing model name, temperature, and base URL (if configured). Keep existing OpenAI/Anthropic behavior unchanged.
- Verification: Add/adjust tests that mock provider classes and assert the Ollama branch is selected with expected constructor args; run `uv run pytest tests/test_agent.py`.

### 4. Update environment template for local provider usage
- File(s): `.env.example`
- Changes: Document `LLM_PROVIDER=ollama` as a valid option; add `OLLAMA_BASE_URL` (and any other needed variable) with sensible defaults/comments; clarify API key expectations for local mode.
- Verification: Manual review that `.env.example` contains complete setup instructions for OpenAI, Anthropic, and Ollama paths.

### 5. Update user documentation and configuration tables
- File(s): `README.md`
- Changes: Update feature list and configuration table to mention Ollama support; revise setup text currently implying only OpenAI/Anthropic keys are valid; add a short local-model usage example (provider + model + optional base URL).
- Verification: README sections are internally consistent (features, requirements, env vars, and examples all mention same provider set).

### 6. Expand automated tests for new provider path
- File(s): `tests/test_agent.py` (and, if needed, `tests/test_main.py` only for env parsing expectations)
- Changes: Add tests for ollama provider selection, default model resolution, and invalid-provider messaging; add isolated tests for `get_llm()` branch behavior via mocking to avoid real network calls.
- Verification: `uv run pytest` passes locally; no existing tests regress for OpenAI/Anthropic.

## Open Questions
- **Step 2:** What should be the default Ollama model name when `MODEL_NAME` is empty (for example `llama3.1:8b`, `qwen2.5-coder`, or another project-preferred model)? Decision: use Qwen 2.5-Coder-14B
- **Step 2/3:** Should `OLLAMA_BASE_URL` default to `http://localhost:11434`, or must users set it explicitly to avoid accidental remote calls? Decision: default is ok
- **Step 3:** Do we need any provider-specific kwargs beyond `model`, `temperature`, and base URL (for example context window/keep_alive), or keep first iteration minimal? Decision: keep first iteration minimal
- **Step 5:** Should README include a compatibility note about tool-calling quality varying by local model family? Decision: Yes, add usefull information

## Out of Scope
- Adding support for non-Ollama local runtimes (vLLM, LM Studio, llama.cpp, etc.).
- Building model download/pull automation (`ollama pull`) into the agent.
- Adding provider-specific routing, fallbacks, or automatic failover between cloud and local models.
- Performance benchmarking or quality evaluation across local models.

---

## Implementation Status
- [x] Step 1 completed: Added `langchain-ollama` dependency and verified import after `uv sync --all-groups`.
- [x] Step 2 completed: Extended provider schema/settings for `ollama`, added `OLLAMA_BASE_URL`, updated validation messaging.
- [x] Step 3 completed: Implemented `ChatOllama` branch in `get_llm()` with model, temperature, and base URL.
- [x] Step 4 completed: Updated `.env.example` with Ollama provider and base URL guidance.
- [x] Step 5 completed: Updated `README.md` features/config/setup and added Ollama usage example + tool-calling note.
- [x] Step 6 completed: Added tests for Ollama settings and LLM factory branching (OpenAI/Anthropic/Ollama mock-based).

Verification run:
- `uv run pytest tests/test_agent.py` → passed
- `uv run pytest` → passed (160 tests)

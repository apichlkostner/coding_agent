from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.application import get_app_or_none
import itertools
import threading
import time

# ── Cat frames (walking) ──────────────────────────────────────────────────────
CAT_FRAMES = ["=^.^=", "=^-^=", "=^o^=", "=^-^="]
cat_cycle = itertools.cycle(CAT_FRAMES)

# ── Hedgehog frames per state ─────────────────────────────────────────────────
HEDGEHOG_FRAMES = {
    "idle":     ["(` ´ʃƪ)", "(' 'ʃƪ)", "(` ´ʃƪ)", "('-'ʃƪ)"],   # gentle blink
    "thinking": ["(°_°ʃƪ)", "(°.°ʃƪ)", "(OwOʃƪ)", "(o.oʃƪ)"],   # wide-eyed wonder
    "working":  ["(>_<ʃƪ)", "(>.<ʃƪ)", "(-_-ʃƪ)", "(>_<ʃƪ)"],   # focused hustle
}
hog_cycles = {state: itertools.cycle(frames) for state, frames in HEDGEHOG_FRAMES.items()}

# ── State tracking (lock-protected for cross-thread access) ───────────────────
_state_lock   = threading.Lock()
hog_state      = "idle"
hog_state_until = 0.0


def get_hog_frame() -> str:
    global hog_state
    with _state_lock:
        if hog_state != "idle" and time.monotonic() >= hog_state_until:
            hog_state = "idle"
        return next(hog_cycles[hog_state])


def set_hog_state(state: str, duration: float = 3.0):
    global hog_state, hog_state_until
    with _state_lock:
        hog_state       = state
        hog_state_until = time.monotonic() + duration


# ── Toolbar (called by prompt_toolkit on every refresh) ───────────────────────
def get_toolbar():
    with _state_lock:
        label = hog_state.capitalize()
    cat = next(cat_cycle)
    hog = get_hog_frame()
    return HTML(
        f"<b>Cat:</b> {cat}   "
        f"<b>Hedgehog:</b> {hog}  <i>({label})</i>"
    )


# ── Status bar that stays visible while processing ────────────────────────────
class StatusBar:
    """
    Draws an animated status bar to the last terminal line while the prompt
    is not active (i.e. during processing).  Uses ANSI codes directly so it
    works without an active prompt_toolkit application.
    """
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"
    SAVE_POS    = "\033[s"
    RESTORE_POS = "\033[u"
    MOVE_LAST   = "\033[999;1H"   # jump to a very-last row
    CLEAR_LINE  = "\033[2K"
    DIM         = "\033[2m"
    RESET       = "\033[0m"

    def __init__(self, refresh: float = 0.3):
        self._refresh  = refresh
        self._stop     = threading.Event()
        self._thread: threading.Thread | None = None

    def _render(self):
        while not self._stop.is_set():
            with _state_lock:
                label = hog_state.capitalize()
            cat = next(cat_cycle)
            hog = get_hog_frame()
            line = f" Cat: {cat}   Hedgehog: {hog}  ({label}) "
            # Only draw when prompt_toolkit app is NOT running
            if get_app_or_none() is None:
                print(
                    f"{self.SAVE_POS}{self.MOVE_LAST}{self.CLEAR_LINE}"
                    f"{self.DIM}{line}{self.RESET}{self.RESTORE_POS}",
                    end="", flush=True,
                )
            self._stop.wait(self._refresh)
        # Clean up the status line when done
        print(
            f"{self.SAVE_POS}{self.MOVE_LAST}{self.CLEAR_LINE}{self.RESTORE_POS}",
            end="", flush=True,
        )

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._render, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()


# ── Agent logic ───────────────────────────────────────────────────────────────
def process(user_input: str) -> str:
    """Fake agent logic — swap in your real LLM call here."""
    if user_input.strip().lower() in ("exit", "quit"):
        return "__quit__"

    set_hog_state("thinking", duration=1.5)
    time.sleep(1.5)           # ← replace with your actual API call

    set_hog_state("working", duration=1.5)
    time.sleep(1.5)           # ← replace with response-assembly time

    return f"Echo → {user_input}"


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    session    = PromptSession()
    status_bar = StatusBar(refresh=0.3)
    status_bar.start()

    print("HedgeAgent ready!  Type 'exit' or 'quit' to leave.\n")

    while True:
        try:
            user_input = session.prompt(
                "You: ",
                bottom_toolbar=get_toolbar,
                refresh_interval=0.3,
            )
        except (EOFError, KeyboardInterrupt):
            break

        # prompt returned → status bar thread takes over the bottom line
        reply = process(user_input)

        if reply == "__quit__":
            status_bar.stop()
            print("Goodbye! (>_< ʃƪ)")
            break

        set_hog_state("idle")
        print(f"Agent: {reply}\n")


if __name__ == "__main__":
    main()
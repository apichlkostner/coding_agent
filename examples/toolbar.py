import asyncio
from collections.abc import Callable

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

status = "Idle"

output_buffer = Buffer()
input_buffer = Buffer(multiline=False)


def get_toolbar_text() -> str:
    return f" Status: {status} "


def append_output(text: str) -> None:
    new_text = output_buffer.text + text + "\n"
    output_buffer.set_document(
        Document(new_text, cursor_position=len(new_text)),
        bypass_readonly=True,
    )


kb = KeyBindings()


@kb.add("c-c")
@kb.add("c-q")
def _(event):
    event.app.exit()


async def process_with_updates(app: Application, user_input: str) -> None:
    global status
    status = "Processing"
    append_output("Starting...")
    await asyncio.sleep(1)
    append_output("Step 1 done")
    await asyncio.sleep(1)
    append_output("Step 2 done")
    status = "Idle"


def make_accept_handler(app: Application) -> Callable:
    def accept(buff: Buffer) -> None:
        text = buff.text.strip()
        append_output(f"You: {text}")
        if text.lower() in ("exit", "quit"):
            app.exit()
            return
        # run processing in the background so the UI (and toolbar) stay responsive
        app.create_background_task(process_with_updates(app, text))
        buff.reset()

    return accept


async def main() -> None:
    output_window = Window(content=BufferControl(buffer=output_buffer), wrap_lines=True)
    separator = Window(height=1, char="-")
    input_window = Window(content=BufferControl(buffer=input_buffer), height=1)
    toolbar_window = Window(
        content=FormattedTextControl(get_toolbar_text),
        height=1,
        style="reverse",
    )

    root_container = HSplit([output_window, separator, input_window, toolbar_window])
    layout = Layout(root_container, focused_element=input_window)

    app = Application(layout=layout, key_bindings=kb, full_screen=True)
    input_buffer.accept_handler = make_accept_handler(app)

    # periodic redraw so the toolbar updates live, replacing refresh_interval
    async def refresher() -> None:
        while True:
            await asyncio.sleep(0.3)
            app.invalidate()

    refresh_task = asyncio.create_task(refresher())
    try:
        await app.run_async()
    finally:
        refresh_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())

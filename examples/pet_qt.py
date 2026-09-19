import threading, queue
from PyQt6.QtWidgets import QApplication, QLabel, QWidget
from PyQt6.QtCore import QTimer, Qt


class HedgehogWindow(QWidget):
    def __init__(self, msg_queue):
        super().__init__()
        self.msg_queue = msg_queue
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # ... setup labels, sprite, timer

    def poll_queue(self):
        while not self.msg_queue.empty():
            event = self.msg_queue.get_nowait()
            self.react_to(event)  # change animation state


def run_hedgehog(q):
    app = QApplication([])
    win = HedgehogWindow(q)
    win.show()
    app.exec()


# In your agent:
q = queue.Queue()
t = threading.Thread(target=run_hedgehog, args=(q,), daemon=True)
t.start()
q.put({"state": "thinking", "text": "Analyzing your code..."})

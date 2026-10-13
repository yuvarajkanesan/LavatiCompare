"""Background tasks for the UI."""
from PySide6.QtCore import QThread, Signal

from . import engine


class TaskWorker(QThread):
    """Runs fn(ctx) on a thread; emits done(result) or failed(exception)."""
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn, ctx=None, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.ctx = ctx or engine.Ctx()

    def run(self):
        try:
            self.done.emit(self.fn(self.ctx))
        except engine.Cancelled:
            self.failed.emit(engine.Cancelled())
        except BaseException as e:      # report instead of dying silently inside the thread
            self.failed.emit(e)

    def cancel(self):
        self.ctx.cancel = True


def run_responsive(fn, *args, busy_text=None):
    """Run a long CPU-bound call on a helper thread while the window keeps repainting.

    User input is ignored meanwhile (so nothing re-enters the view), but Windows never marks the app as
    'not responding'. Returns fn's result or re-raises its exception."""
    import threading
    import time
    from PySide6.QtCore import QEventLoop
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication
    box = {}

    def target():
        try:
            box["v"] = fn(*args)
        except BaseException as e:               # handed back to the caller below
            box["e"] = e
    th = threading.Thread(target=target, daemon=True)
    th.start()
    app = QApplication.instance()
    if app is not None:
        app.setOverrideCursor(QCursor(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.BusyCursor))
    try:
        while th.is_alive():
            if app is not None:
                app.processEvents(QEventLoop.ExcludeUserInputEvents, 30)
            th.join(0.03)
    finally:
        if app is not None:
            app.restoreOverrideCursor()
    if "e" in box:
        raise box["e"]
    return box["v"]

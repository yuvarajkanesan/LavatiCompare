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

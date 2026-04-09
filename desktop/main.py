import sys
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from window import SaveManagerWindow


def _global_excepthook(exc_type, exc_value, exc_tb):
    traceback.print_exception(exc_type, exc_value, exc_tb)
    try:
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        QMessageBox.critical(None, "Unhandled Error", msg[:4000])
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    sys.excepthook = _global_excepthook
    window = SaveManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

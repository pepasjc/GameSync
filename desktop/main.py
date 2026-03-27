import sys
from PyQt6.QtWidgets import QApplication
from window import SaveManagerWindow


def main():
    app = QApplication(sys.argv)
    window = SaveManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

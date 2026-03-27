from PyQt6.QtWidgets import QDialog, QMainWindow, QTabWidget

from config import load_config, save_config
from dialogs.config_dialog import ConfigDialog
from tabs.server_saves_tab import ServerSavesTab
from tabs.profiles_tab import ProfilesTab
from tabs.sync_tab import SyncTab
from tabs.normalizer_tab import RomNormalizerTab
from tabs.rom_collection_tab import RomCollectionTab


class SaveManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Save Manager")
        self.setMinimumSize(1000, 650)
        self._init_ui()
        self._restore_state()

    def _init_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.server_tab = ServerSavesTab()
        self.profiles_tab = ProfilesTab()
        self.sync_tab = SyncTab(self.profiles_tab)
        self.normalizer_tab = RomNormalizerTab()
        self.collection_tab = RomCollectionTab()

        self.tabs.addTab(self.server_tab, "Server Saves")
        self.tabs.addTab(self.profiles_tab, "Sync Profiles")
        self.tabs.addTab(self.sync_tab, "Sync")
        self.tabs.addTab(self.normalizer_tab, "ROM Normalizer")
        self.tabs.addTab(self.collection_tab, "ROM Collection")

        # Refresh sync profile list whenever the Sync tab is shown
        self.tabs.currentChanged.connect(self._on_tab_changed)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction("Refresh Server Saves", self.server_tab.load_saves)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        tools_menu = menubar.addMenu("Tools")
        tools_menu.addAction("Config...", self._show_config)

    def _restore_state(self):
        cfg = load_config()
        ui = cfg.get("ui_state", {})

        # Window geometry
        if "window" in ui:
            w = ui["window"]
            self.resize(w.get("width", 1100), w.get("height", 700))
            if "x" in w and "y" in w:
                self.move(w["x"], w["y"])
        else:
            self.resize(1100, 700)

        # Active tab
        self.tabs.setCurrentIndex(ui.get("active_tab", 0))

        # Per-tab state
        self.server_tab.load_ui_state(ui.get("server_saves", {}))
        self.sync_tab.load_ui_state(ui.get("sync", {}))
        self.normalizer_tab.load_ui_state(ui.get("rom_normalizer", {}))
        self.collection_tab.load_ui_state(ui.get("rom_collection", {}))

    def _save_state(self):
        cfg = load_config()
        geo = self.geometry()
        cfg["ui_state"] = {
            "window": {
                "x": geo.x(), "y": geo.y(),
                "width": geo.width(), "height": geo.height(),
            },
            "active_tab": self.tabs.currentIndex(),
            "server_saves": self.server_tab.save_ui_state(),
            "sync": self.sync_tab.save_ui_state(),
            "rom_normalizer": self.normalizer_tab.save_ui_state(),
            "rom_collection": self.collection_tab.save_ui_state(),
        }
        save_config(cfg)

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

    def _on_tab_changed(self, index: int):
        if self.tabs.widget(index) is self.sync_tab:
            self.sync_tab._refresh_profile_list()

    def _show_config(self):
        dialog = ConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.server_tab.load_saves()

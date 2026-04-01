"""Shared gamepad polling helpers for modal dialogs."""

from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication

try:
    import pygame

    _PYGAME_OK = True
except ImportError:
    pygame = None
    _PYGAME_OK = False


def modal_gamepad_key(button_idx: int) -> Qt.Key | None:
    """Map Steam Deck face buttons to dialog key handlers."""
    return {
        0: Qt.Key.Key_A,
        1: Qt.Key.Key_B,
        2: Qt.Key.Key_X,
        3: Qt.Key.Key_Y,
    }.get(button_idx)


class GamepadModalMixin:
    """Adds direct pygame-based gamepad polling to modal dialogs."""

    def _init_gamepad_modal(self) -> None:
        self._gamepad_timer = None
        self._modal_btn_state: dict[int, bool] = {}
        self._modal_joystick = None
        if not _PYGAME_OK:
            return
        try:
            pygame.init()
            pygame.joystick.init()
            self._try_grab_modal_joystick()
            self._gamepad_timer = QTimer(self)
            self._gamepad_timer.setInterval(16)
            self._gamepad_timer.timeout.connect(self._poll_modal_gamepad)
            self._gamepad_timer.start()
        except Exception:
            self._modal_joystick = None

    def _try_grab_modal_joystick(self) -> None:
        if not _PYGAME_OK:
            self._modal_joystick = None
            return
        try:
            count = pygame.joystick.get_count()
            if count > 0:
                js = pygame.joystick.Joystick(0)
                js.init()
                self._modal_joystick = js
                self._prime_modal_button_state()
            else:
                self._modal_joystick = None
        except Exception:
            self._modal_joystick = None

    def _prime_modal_button_state(self) -> None:
        """Capture current button states so held buttons don't auto-fire on open."""
        if not _PYGAME_OK or self._modal_joystick is None:
            return
        try:
            pygame.event.pump()
        except Exception:
            return
        for idx in range(4):
            try:
                self._modal_btn_state[idx] = bool(self._modal_joystick.get_button(idx))
            except Exception:
                self._modal_btn_state[idx] = False

    def _poll_modal_gamepad(self) -> None:
        if QApplication.activeModalWidget() is not self:
            return
        if not _PYGAME_OK:
            return
        if self._modal_joystick is None:
            self._try_grab_modal_joystick()
            if self._modal_joystick is None:
                return
        try:
            pygame.event.pump()
        except Exception:
            return

        for idx in range(4):
            try:
                current = bool(self._modal_joystick.get_button(idx))
            except Exception:
                current = False
            previous = self._modal_btn_state.get(idx, False)
            self._modal_btn_state[idx] = current
            if current and not previous:
                key = modal_gamepad_key(idx)
                if key is not None:
                    self.handle_gamepad_key(key)

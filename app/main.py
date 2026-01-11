# app/main.py
# Start with:  python -m app.main

import sys
import os
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, Slot, QTimer, QMetaObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QStatusBar,
    QToolBar,
    QMessageBox,
    QFileDialog,
)

from app.stt_whisperx import WhisperXConfig, WhisperXTranscriber

class TranscriptBus(QObject):
    """
    Thread-safe Bridge: background workers -> GUI.
    """
    new_text = Signal(str)
    status = Signal(str)
    call_started = Signal(str)
    call_ended = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, bus: TranscriptBus):
        super().__init__()
        self.bus = bus

        self.setWindowTitle("continuous_v2t – Demo GUI (WhisperX Offline STT)")
        self.resize(1100, 650)

        self._current_call_label = "Kein aktiver Call"
        self._transcript_lines: list[str] = []
        self._last_error: str = ""

        self._build_ui()
        self._connect_signals()
        self._set_ready_state()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        main_layout = QHBoxLayout(central)
        self.setCentralWidget(central)

        # Left panel: Calls + controls
        left_layout = QVBoxLayout()
        lbl_calls = QLabel("Anrufe / Dateien")
        lbl_calls.setStyleSheet("font-weight: bold;")

        self.calls_list = QListWidget()
        self.calls_list.addItem("— (noch keine Einträge) —")

        self.btn_start_demo = QPushButton("Demo-Transkription starten")
        self.btn_stop_demo = QPushButton("Demo-Transkription stoppen")
        self.btn_stop_demo.setEnabled(False)

        self.btn_transcribe_file = QPushButton("Audio transkribieren…")

        self.btn_clear = QPushButton("Transkript löschen")
        self.btn_export = QPushButton("Exportieren…")

        left_layout.addWidget(lbl_calls)
        left_layout.addWidget(self.calls_list)
        left_layout.addSpacing(10)
        left_layout.addWidget(self.btn_start_demo)
        left_layout.addWidget(self.btn_stop_demo)
        left_layout.addWidget(self.btn_transcribe_file)
        left_layout.addSpacing(10)
        left_layout.addWidget(self.btn_clear)
        left_layout.addWidget(self.btn_export)
        left_layout.addStretch()

        # Right panel: transcript
        right_layout = QVBoxLayout()
        self.lbl_transcript = QLabel("Transkript")
        self.lbl_transcript.setStyleSheet("font-weight: bold;")

        self.transcript_edit = QPlainTextEdit()
        self.transcript_edit.setReadOnly(True)
        self.transcript_edit.setPlaceholderText(
            "Hier erscheint die Transkription.\n"
            "- Demo schreibt Beispieltext.\n"
            "- 'Audio transkribieren…' nutzt WhisperX offline."
        )

        right_layout.addWidget(self.lbl_transcript)
        right_layout.addWidget(self.transcript_edit)

        main_layout.addLayout(left_layout, 1)
        main_layout.addLayout(right_layout, 2)

        # Toolbar
        toolbar = QToolBar("Aktionen", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        act_about = QAction("About", self)
        act_quit = QAction("Beenden", self)
        toolbar.addAction(act_about)
        toolbar.addSeparator()
        toolbar.addAction(act_quit)

        act_about.triggered.connect(self._show_about)
        act_quit.triggered.connect(self.close)

        # Statusbar
        self.setStatusBar(QStatusBar(self))

    def _connect_signals(self) -> None:
        # GUI buttons
        self.btn_start_demo.clicked.connect(self._start_demo)
        self.btn_stop_demo.clicked.connect(self._stop_demo)
        self.btn_transcribe_file.clicked.connect(self._transcribe_audio_file)
        self.btn_clear.clicked.connect(self._clear_transcript)
        self.btn_export.clicked.connect(self._export_transcript)

        # Bus signals (from workers)
        self.bus.new_text.connect(self._on_new_text)
        self.bus.status.connect(self._on_status)
        self.bus.call_started.connect(self._on_call_started)
        self.bus.call_ended.connect(self._on_call_ended)

    # ---------------- State helpers ----------------

    def _set_ready_state(self) -> None:
        self._on_status("Bereit.")
        self._update_transcript_header()

    def _update_transcript_header(self) -> None:
        self.lbl_transcript.setText(f"Transkript – {self._current_call_label}")

    def _append_transcript(self, line: str) -> None:
        self._transcript_lines.append(line)
        self.transcript_edit.appendPlainText(line)

    # ---------------- Slots (Bus) ----------------

    @Slot(str)
    def _on_new_text(self, text: str) -> None:
        if "s–" in text[:20]:
            self._append_transcript(text)
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            self._append_transcript(f"[{ts}] {text}")

    @Slot(str)
    def _on_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    @Slot(str)
    def _on_call_started(self, call_label: str) -> None:
        self._current_call_label = call_label
        self._update_transcript_header()
        self._on_status(f"Start: {call_label}")

        if self.calls_list.count() == 1 and self.calls_list.item(0).text().startswith("—"):
            self.calls_list.clear()
        self.calls_list.insertItem(0, f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} – {call_label}")

    @Slot(str)
    def _on_call_ended(self, call_label: str) -> None:
        self._on_status(f"Fertig: {call_label}")
        self._current_call_label = "Kein aktiver Call"
        self._update_transcript_header()

    # ---------------- Demo generator ----------------

    def _start_demo(self) -> None:
        self.btn_start_demo.setEnabled(False)
        self.btn_stop_demo.setEnabled(True)

        self.bus.call_started.emit("Demo-Call: +49 30 123456 (simuliert)")
        self._append_transcript("[Info] Demo-Transkription gestartet …")

        self._demo_lines = [
            "Guten Tag, hier ist Herr Müller von Firma Beispiel.",
            "Ich hätte eine Frage zu Ihrer letzten Lieferung.",
            "Könnten Sie mir bitte die Seriennummer noch einmal nennen?",
            "Vielen Dank, das hilft mir sehr weiter.",
            "Dann wünsche ich Ihnen noch einen schönen Tag.",
        ]
        self._demo_idx = 0

        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._emit_demo_line)
        self._demo_timer.start(900)

        self.bus.status.emit("Demo läuft.")

    def _emit_demo_line(self) -> None:
        if self._demo_idx >= 12:
            self._stop_demo()
            return

        line = self._demo_lines[self._demo_idx % len(self._demo_lines)]
        self.bus.new_text.emit(line)
        self._demo_idx += 1

    def _stop_demo(self) -> None:
        if hasattr(self, "_demo_timer") and self._demo_timer.isActive():
            self._demo_timer.stop()

        self.btn_start_demo.setEnabled(True)
        self.btn_stop_demo.setEnabled(False)

        self._append_transcript("[Info] Demo-Transkription beendet.")
        self.bus.call_ended.emit("Demo-Call")
        self.bus.status.emit("Bereit.")

    # ---------------- WhisperX file transcription ----------------

    def _transcribe_audio_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Audio auswählen",
            "",
            "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac);;All files (*.*)",
        )
        if not path:
            return
        
        self._transcribe_audio_file_direct(path)

    def _transcribe_audio_file_direct(self, path: str) -> None:
        """Direkt-Start ohne FileDialog (für CLI/Tests)"""
        if not path or not os.path.exists(path):
            self.bus.status.emit(f"Datei nicht gefunden: {path}")
            return
        
        # Stop demo if running
        if hasattr(self, "_demo_timer") and self._demo_timer.isActive():
            self._stop_demo()

        self._clear_transcript()

        cfg = WhisperXConfig(
            model_size="small",
            language="de",
            device="cpu",
            compute_type="int8",
            batch_size=8,
        )

        self.btn_transcribe_file.setEnabled(False)
        self.bus.status.emit("WhisperX: Transkription gestartet …")
        self.bus.call_started.emit(f"Datei: {path}")

        def worker():
            try:
                def on_status(msg: str):
                    self.bus.status.emit(msg)

                def on_segment(seg: dict):
                    start = float(seg.get("start", 0.0) or 0.0)
                    end = float(seg.get("end", 0.0) or 0.0)
                    text = (seg.get("text") or "").strip()
                    if text:
                        self.bus.new_text.emit(f"{start:7.2f}s–{end:7.2f}s: {text}")

                tx = WhisperXTranscriber(cfg, on_status=on_status, on_segment=on_segment)
                tx.transcribe_file(path)

                self.bus.call_ended.emit(f"Datei: {path}")
                self.bus.status.emit("WhisperX: fertig.")
            except Exception as e:
                self.bus.status.emit(f"WhisperX Fehler: {e!r}")
                self._show_error_safe(repr(e))
            finally:
                self._enable_transcribe_button_safe()

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Safe GUI updates from worker thread ----------------

    def _enable_transcribe_button_safe(self):
        QMetaObject.invokeMethod(self, "_enable_transcribe_button", Qt.QueuedConnection)

    @Slot()
    def _enable_transcribe_button(self):
        self.btn_transcribe_file.setEnabled(True)

    def _show_error_safe(self, msg: str):
        self._last_error = msg
        QMetaObject.invokeMethod(self, "_show_error_dialog", Qt.QueuedConnection)

    @Slot()
    def _show_error_dialog(self):
        QMessageBox.critical(self, "WhisperX Fehler", self._last_error)

    # ---------------- Actions ----------------

    def _clear_transcript(self) -> None:
        self._transcript_lines = []
        self.transcript_edit.clear()
        self.bus.status.emit("Transkript gelöscht.")

    def _export_transcript(self) -> None:
        if not self._transcript_lines:
            QMessageBox.information(self, "Export", "Kein Transkript zum Exportieren vorhanden.")
            return

        default_name = f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Transkript exportieren",
            default_name,
            "Text (*.txt);;All files (*.*)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._transcript_lines) + "\n")
            self.bus.status.emit(f"Exportiert: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export fehlgeschlagen", str(e))

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "continuous_v2t\n\n"
            "PySide6 GUI Demo für Voice-to-Text.\n"
            "Aktuell: Offline-Transkription via WhisperX (Datei).\n"
            "Start: python -m app.main\n",
        )

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            "Beenden",
            "Möchtest du die Anwendung wirklich beenden?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    bus = TranscriptBus()
    win = MainWindow(bus)
    win.show()
    
    # CLI-Parameter oder Hardcoded-Test
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
    else:
        test_file = r"C:\temp\BayrischTest.wav"
    
    # Auto-start Transkription wenn Datei existiert
    if os.path.exists(test_file):
        QTimer.singleShot(500, lambda: win._transcribe_audio_file_direct(test_file))
    
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
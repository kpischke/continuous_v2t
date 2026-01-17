# app/live_transcriber.py
from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import wave
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from app.audio_capture import MicrophoneCapture
from app.stt_whisperx import WhisperXConfig, WhisperXTranscriber


@dataclass
class LiveParams:
    # Audio format assumptions (müssen zum MicrophoneCapture passen)
    sample_rate: int = 16000
    channels: int = 1
    sampwidth_bytes: int = 2  # int16

    # Fensterung (pseudo-live)
    window_seconds: float = 5.0
    overlap_seconds: float = 1.5
    capture_chunk_ms: int = 100

    # Robustheit
    queue_timeout_s: float = 0.2
    status_interval_s: float = 0.5
    max_backlog_chunks: int = 200

    # Graceful stop / flush
    min_flush_seconds: float = 0.6

    # Silence gate
    silence_rms_threshold: float = 80.0
    silence_peak_threshold: int = 900

    # Debug
    log_audio_level: bool = True


class LiveTranscriber:
    """
    Kontinuierliche (pseudo-)Live-Transkription von Mikrofon-Audio.
    WhisperX wird fensterweise auf WAV-Snapshots angewendet.

    Enthält detailliertes File-Logging nach ../live_debug.log (relativ zu diesem File).
    """

    def __init__(
        self,
        on_text: Callable[[str], None],
        on_status: Callable[[str], None],
        config: WhisperXConfig,
        params: Optional[LiveParams] = None,
    ):
        self._started_emitting = False

        self.on_text = on_text
        self.on_status = on_status
        self.cfg = config
        self.params = params or LiveParams()

        self._running = False
        self._stop_event = threading.Event()

        self._capture: Optional[MicrophoneCapture] = None
        self._collector_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None

        self._transcribe_q: "queue.Queue[bytes]" = queue.Queue(maxsize=20)
        self._last_status_ts = 0.0

        self._transcriber: Optional[WhisperXTranscriber] = None

        # Ringbuffer + Lock, damit stop() sauber flushen kann
        self._ring = bytearray()
        self._ring_lock = threading.Lock()

        # Dedupe: nur exakte Duplikate
        self._last_emitted = ""

        # --- File logging ---
        self._logger = logging.getLogger("continuous_v2t.live")
        self._logger.setLevel(logging.INFO)

        # Log-Pfad: Projektroot/live_debug.log (ein Verzeichnis über app/)
        log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "live_debug.log"))
        self._log_path = log_path

        if not any(isinstance(h, RotatingFileHandler) for h in self._logger.handlers):
            handler = RotatingFileHandler(
                log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

        self._logger.info("Logger initialized. Writing to %s", self._log_path)

    # -----------------------------
    # Logging helpers
    # -----------------------------

    def _log_status(self, msg: str) -> None:
        try:
            self._logger.info(msg)
        except Exception:
            pass
        try:
            self.on_status(msg)
        except Exception:
            pass

    def _throttled_status(self, msg: str) -> None:
        now = time.time()
        if now - self._last_status_ts >= self.params.status_interval_s:
            self._last_status_ts = now
            self._log_status(msg)

    # -----------------------------
    # Public controls
    # -----------------------------

    def start(self) -> None:
        if self._running:
            return

        self._logger.info("start() called")

        self._running = True
        self._stop_event.clear()

        with self._ring_lock:
            self._ring = bytearray()

        # Capture starten
        self._capture = MicrophoneCapture(
            sample_rate=self.params.sample_rate,
            chunk_duration_ms=self.params.capture_chunk_ms,
        )
        self._capture.start()

        self._logger.info("MicrophoneCapture started (sr=%s, chunk_ms=%s)",
                            self.params.sample_rate, self.params.capture_chunk_ms)

        # WhisperX Transcriber (cfg positional, wie in deinem stt_whisperx.py)
        self._transcriber = WhisperXTranscriber(
            self.cfg,
            on_status=self._on_status,
            on_segment=self._on_segment,
        )
        
        self._log_status("Preload: lade WhisperX Modell…")
        self._transcriber.preload()
        self._log_status("Preload: Modell geladen.")

        self._logger.info("WhisperXTranscriber created (model=%s, device=%s, compute=%s, batch=%s)",
                        getattr(self.cfg, "model_size", None),
                        getattr(self.cfg, "device", None),
                        getattr(self.cfg, "compute_type", None),
                        getattr(self.cfg, "batch_size", None))

        # Threads starten
        self._collector_thread = threading.Thread(target=self._collector_loop, daemon=True)
        self._transcribe_thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self._collector_thread.start()
        self._transcribe_thread.start()

        self._log_status(
            f"Live-Transkription läuft. Fenster={self.params.window_seconds:.1f}s, "
            f"Overlap={self.params.overlap_seconds:.1f}s. Log: {self._log_path}"
        )

    def stop(self) -> None:
        """
        Graceful stop:
        - Audioaufnahme stoppen
        - Rest-Audio aus der Capture-Queue in den Ringbuffer ziehen
        - Letzten Snapshot flushen (auch wenn kleiner als window_seconds, aber >= min_flush_seconds)
        - Worker fertig rechnen lassen
        """
        if not self._running:
            return

        self._logger.info("stop() called (graceful)")

        p = self.params
        self._log_status("Live: stoppe (graceful)…")

        # 1) Capture stoppen
        try:
            if self._capture:
                self._capture.stop()
                self._logger.info("MicrophoneCapture stopped")
        except Exception as e:
            self._logger.exception("Error stopping capture: %s", e)

        # 2) Restliche Audio-Queue drainen
        drained = 0
        if self._capture is not None:
            try:
                while True:
                    data = self._capture.audio_queue.get_nowait()
                    if not data:
                        continue
                    with self._ring_lock:
                        self._ring.extend(data)
                    drained += 1
            except Exception:
                pass

        if drained:
            self._log_status(f"Live: {drained} Rest-Frames aus Queue übernommen.")

        # 3) Flush, wenn genug Audio vorhanden
        sample_bytes_per_sec = p.sample_rate * p.channels * p.sampwidth_bytes
        min_flush_bytes = int(p.min_flush_seconds * sample_bytes_per_sec)

        flush_bytes: bytes = b""
        with self._ring_lock:
            ring_len = len(self._ring)
            if ring_len >= min_flush_bytes:
                flush_bytes = bytes(self._ring)
            self._ring = bytearray()

        self._logger.info("stop(): ring_len=%s, min_flush_bytes=%s, will_flush=%s",
                          ring_len, min_flush_bytes, bool(flush_bytes))

        # Flush-Silence-Gate
        if flush_bytes:
            samples = np.frombuffer(flush_bytes, dtype=np.int16)
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if samples.size else 0.0
            peak = int(np.max(np.abs(samples))) if samples.size else 0
            flush_rms_thr = p.silence_rms_threshold * 0.8
            flush_peak_thr = int(p.silence_peak_threshold * 1.3)

            if (rms < flush_rms_thr) and (peak < flush_peak_thr):
                self._log_status(f"Live: Skip flush (too silent, rms={rms:.1f}, peak={peak})")
                flush_bytes = b""

        if flush_bytes:
            try:
                try:
                    self._transcribe_q.put_nowait(flush_bytes)
                except queue.Full:
                    try:
                        _ = self._transcribe_q.get_nowait()
                    except Exception:
                        pass
                    self._transcribe_q.put_nowait(flush_bytes)
                self._log_status("Live: letzten Buffer flush zur Transkription übergeben.")
            except Exception as e:
                self._logger.exception("Error queueing flush chunk: %s", e)
        else:
            self._log_status("Live: nichts zu flushen (zu wenig Audio oder zu leise).")

        # 4) Stop-Signal setzen und Threads auslaufen lassen
        self._running = False
        self._stop_event.set()

        for tname, t in (("collector", self._collector_thread), ("transcribe", self._transcribe_thread)):
            if t and t.is_alive():
                self._logger.info("Joining %s thread...", tname)
                t.join(timeout=15.0)
                self._logger.info("%s thread alive=%s", tname, t.is_alive())

        self._log_status("Live-Transkription gestoppt.")

    # -----------------------------
    # Intern: Callbacks
    # -----------------------------

    def _on_status(self, msg: str) -> None:
        # WhisperX meldet relativ viel; wir loggen es in Datei (nicht in UI flooden)
        try:
            self._logger.info("WhisperX: %s", msg)
        except Exception:
            pass

    def _on_segment(self, seg: dict) -> None:
        text = (seg.get("text") or "").strip()
        if not text:
            return

        # Minimal-Dedupe: exakte Duplikate
        norm = text.lower().strip(" \t\r\n.,!?;:\"'()[]{}")
        last = self._last_emitted.lower().strip(" \t\r\n.,!?;:\"'()[]{}")

        if norm == last:
            self._logger.info("Segment deduped (normalized): %s", text)
            return

        self._last_emitted = text
        self._logger.info("Segment emit: %s", text)

        try:
            self.on_text(text)
        except Exception as e:
            self._logger.exception("on_text callback failed: %s", e)

    # -----------------------------
    # Intern: Collector
    # -----------------------------

    def _collector_loop(self) -> None:
        assert self._capture is not None

        p = self.params
        window_bytes = int(p.sample_rate * p.window_seconds * p.sampwidth_bytes * p.channels)
        overlap_bytes = int(p.sample_rate * p.overlap_seconds * p.sampwidth_bytes * p.channels)

        bytes_in = 0
        chunks_in = 0
        last_stat = time.time()

        self._logger.info("Collector started (window_bytes=%s, overlap_bytes=%s)", window_bytes, overlap_bytes)

        while self._running and not self._stop_event.is_set():
            try:
                # Backlog-Schutz
                qsize = self._capture.audio_queue.qsize()
                if qsize > p.max_backlog_chunks:
                    drop = qsize - (p.max_backlog_chunks // 2)
                    for _ in range(drop):
                        try:
                            self._capture.audio_queue.get_nowait()
                        except Exception:
                            break
                    self._log_status(f"Warnung: Audio-Backlog ({qsize}) reduziert (drop={drop}).")

                data = self._capture.audio_queue.get(timeout=p.queue_timeout_s)
                if not data:
                    continue

                with self._ring_lock:
                    self._ring.extend(data)
                    ring_len = len(self._ring)

                bytes_in += len(data)
                chunks_in += 1

                now = time.time()
                if now - last_stat >= p.status_interval_s:
                    self._throttled_status(
                        f"Collector: buffer={ring_len} bytes, in={bytes_in} B/s, chunks/s≈{chunks_in}, qsize={qsize}"
                    )
                    bytes_in = 0
                    chunks_in = 0
                    last_stat = now

                if ring_len >= window_bytes:
                    with self._ring_lock:
                        snapshot = bytes(self._ring[-window_bytes:])

                        # Overlap behalten
                        if overlap_bytes > 0:
                            self._ring = bytearray(self._ring[-overlap_bytes:])
                        else:
                            self._ring = bytearray()

                    try:
                        self._transcribe_q.put_nowait(snapshot)
                        self._logger.info("Collector queued snapshot (bytes=%s). transcribe_q=%s",
                                          len(snapshot), self._transcribe_q.qsize())
                    except queue.Full:
                        # Drop oldest and retry
                        try:
                            _ = self._transcribe_q.get_nowait()
                        except Exception:
                            pass
                        try:
                            self._transcribe_q.put_nowait(snapshot)
                            self._logger.info("Collector queued snapshot after drop (bytes=%s). transcribe_q=%s",
                                              len(snapshot), self._transcribe_q.qsize())
                        except Exception:
                            self._logger.warning("Collector could not queue snapshot (queue full). Dropping snapshot.")

            except queue.Empty:
                continue
            except Exception as e:
                self._logger.exception("Collector error: %s", e)
                if self._running:
                    self._log_status(f"Live-Collector-Fehler: {e}")
                break

        self._logger.info("Collector exited")

    # -----------------------------
    # Intern: Transcription worker
    # -----------------------------

    def _transcribe_loop(self) -> None:
        p = self.params
        assert self._transcriber is not None

        self._logger.info("Transcribe worker started (silence_rms_threshold=%s)", p.silence_rms_threshold)

        # Beim Stop noch ausstehende Queue-Elemente abarbeiten.
        while (not self._stop_event.is_set()) or (not self._transcribe_q.empty()):
            try:
                pcm_bytes = self._transcribe_q.get(timeout=0.2)
            except queue.Empty:
                continue

            if not pcm_bytes:
                continue

            # --- Silence-Gate / Audio-Level (immer berechnen) ---
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            if samples.size:
                peak = int(np.max(np.abs(samples)))
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            else:
                peak = 0
                rms = 0.0
                
        # --- Start-Gate: erst nach klarer Sprache emitten ---
            if not self._started_emitting:
                if peak < 1500 and rms < 120:
                    self._logger.info(
                        "Worker: START-GATE skip rms=%.1f peak=%s", rms, peak
                    )
                    continue
            self._started_emitting = True
        # --- Ende Start-Gate ---

            if p.log_audio_level:
                self._throttled_status(f"Worker: audio peak={peak}, rms={rms:.1f}, bytes={len(pcm_bytes)}")

            if samples.size and (rms < p.silence_rms_threshold) and (peak < p.silence_peak_threshold):
                self._logger.info(
                    "Worker: SKIP (too silent) rms=%.1f peak=%s bytes=%s", rms, peak, len(pcm_bytes)
                )
                continue

            # --- Ende Silence-Gate ---

            self._log_status(f"Worker: transcribe chunk bytes={len(pcm_bytes)} peak={peak} rms={rms:.1f}")

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name

                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(p.channels)
                    wf.setsampwidth(p.sampwidth_bytes)
                    wf.setframerate(p.sample_rate)
                    wf.writeframes(pcm_bytes)

                self._logger.info("Worker: wrote wav %s (bytes=%s)", tmp_path, len(pcm_bytes))

                # transcribe_file triggert on_segment callbacks
                _ = self._transcriber.transcribe_file(tmp_path)

                self._logger.info("Worker: transcribe_file finished")

            except Exception as e:
                self._logger.exception("Worker: transcribe error: %s", e)
                self._log_status(f"Live: Transkriptionsfehler: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                        self._logger.info("Worker: deleted tmp wav %s", tmp_path)
                    except Exception:
                        pass

        self._logger.info("Transcribe worker exited")

# app/stt_whisperx.py
from __future__ import annotations
import os
import sys

# Für EXE: Cache auf gebundelte Modelle umleiten
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    os.environ['HF_HOME'] = os.path.join(base_path, '.cache', 'huggingface')
    os.environ['TORCH_HOME'] = os.path.join(base_path, '.cache', 'torch')
    os.environ['TRANSFORMERS_CACHE'] = os.path.join(base_path, '.cache', 'huggingface', 'hub')
    

from dataclasses import dataclass
from typing import Callable, Optional

# ---- PyTorch Workaround (optional) ----
# Hinweis: Der pickle.Unpickler Patch ist in Python 3.12 nicht zuverlässig (immutable type).
# Daher nur torch.load Workaround, falls du ihn wirklich brauchst.
try:
    import torch

    _original_load = torch.load

    def _unsafe_load(*args, **kwargs):
        # Erzwinge weights_only=False für alle Loads
        kwargs["weights_only"] = False
        return _original_load(*args, **kwargs)

    torch.load = _unsafe_load
except Exception as e:
    print(f"Warning: Could not patch torch.load: {e}")

import numpy as np
import soundfile as sf
import whisperx


@dataclass
class WhisperXConfig:
    model_size: str = "small"
    language: Optional[str] = "de"
    device: str = "cpu"
    compute_type: str = "int8"
    batch_size: int = 4
    vad_options: Optional[dict] = None


class WhisperXTranscriber:
    """
    Offline-Transkription einer Audiodatei.
    Für WAV (insb. aus dem Live-Pfad) wird soundfile verwendet (kein ffmpeg).
    """

    def __init__(
        self,
        cfg: WhisperXConfig,
        on_status: Optional[Callable[[str], None]] = None,
        on_segment: Optional[Callable[[dict], None]] = None,
    ):
        self.cfg = cfg
        self.on_status = on_status or (lambda _: None)
        self.on_segment = on_segment or (lambda _: None)
        self._model = None

    def _ensure_model(self) -> None:
        if self._model is None:
            self.on_status(
                f"WhisperX: lade Modell '{self.cfg.model_size}' "
                f"(device={self.cfg.device}, compute_type={self.cfg.compute_type}) …"
            )
            self._model = whisperx.load_model(
                self.cfg.model_size,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
                language=self.cfg.language,
                vad_method="silero",
                vad_options=self.cfg.vad_options,
            )
            self.on_status("WhisperX: Modell geladen.")

    def preload(self) -> None:
        self._ensure_model()

    @staticmethod
    def _load_wav_soundfile(path: str) -> np.ndarray:
        # soundfile liefert float32/float64; wir zwingen float32
        audio, sr = sf.read(path, dtype="float32", always_2d=False)

        # Mono erzwingen
        if isinstance(audio, np.ndarray) and audio.ndim == 2:
            # (samples, channels) -> mono
            audio = audio.mean(axis=1).astype(np.float32, copy=False)

        if sr != 16000:
            # Dein Live-Pfad schreibt 16kHz WAV. Wenn doch abweichend, lieber hart failen,
            # statt schlecht zu resamplen "irgendwie".
            raise RuntimeError(f"Unerwartete Sample-Rate {sr} Hz (erwartet 16000 Hz).")

        return audio

    def transcribe_file(self, audio_path: str) -> dict:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio-Datei nicht gefunden: {audio_path}")

        try:
            self._ensure_model()
        except Exception as e:
            raise RuntimeError(f"Modell-Laden fehlgeschlagen: {e}") from e

        self.on_status("WhisperX: lade Audio …")
        try:
            # Für WAV direkt per soundfile (ffmpeg-frei).
            # Wenn du auch mp3/m4a willst, musst du entweder ffmpeg nutzen oder hier fallunterscheiden.
            if audio_path.lower().endswith(".wav"):
                audio = self._load_wav_soundfile(audio_path)
            else:
                audio = whisperx.load_audio(audio_path)

        except Exception as e:
            raise RuntimeError(f"Audio-Laden fehlgeschlagen: {e}") from e

        self.on_status("WhisperX: transkribiere …")
        try:
            result = self._model.transcribe(
                audio,
                batch_size=self.cfg.batch_size,
                language=self.cfg.language,
            )
        except Exception as e:
            raise RuntimeError(f"Transkription fehlgeschlagen: {e}") from e

        segments = result.get("segments", []) or []
        for seg in segments:
            self.on_segment(seg)

        self.on_status("WhisperX: Transkription fertig.")
        return result

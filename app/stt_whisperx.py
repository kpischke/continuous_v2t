# app/stt_whisperx.py

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

# ---- PyTorch 2.6+ VOLLSTÄNDIGER Workaround ----
try:
    import torch
    import pickle
    
    # Setze torch.load global auf unsichere aber funktionierende Variante
    _original_load = torch.load
    
    def _unsafe_load(*args, **kwargs):
        # Erzwinge weights_only=False für alle Loads
        kwargs['weights_only'] = False
        return _original_load(*args, **kwargs)
    
    torch.load = _unsafe_load
    
    # Auch pickle.Unpickler patchen (falls WhisperX direkt pickle nutzt)
    pickle.Unpickler.find_class = lambda self, module, name: getattr(__import__(module, fromlist=[name]), name)
    
except Exception as e:
    print(f"Warning: Could not patch torch.load: {e}")

import whisperx


@dataclass
class WhisperXConfig:
    model_size: str = "small"
    language: Optional[str] = "de"
    device: str = "cpu"
    compute_type: str = "int8"
    batch_size: int = 8
    vad_onset: float = 0.5


class WhisperXTranscriber:
    """
    Offline-Transkription einer Audiodatei (wav/mp3/m4a/…).
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
        
    def _ensure_model(self):
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
            )
            self.on_status("WhisperX: Modell geladen.")

    def transcribe_file(self, audio_path: str) -> dict:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio-Datei nicht gefunden: {audio_path}")

        try:
            self._ensure_model()
        except Exception as e:
            raise RuntimeError(f"Modell-Laden fehlgeschlagen: {e}") from e

        self.on_status("WhisperX: lade Audio …")
        try:
            audio = whisperx.load_audio(audio_path)
        except Exception as e:
            raise RuntimeError(f"Audio-Laden fehlgeschlagen: {e}") from e

        self.on_status("WhisperX: transkribiere …")
        try:
            result = self._model.transcribe(
                audio, 
                batch_size=self.cfg.batch_size
            )
        except Exception as e:
            raise RuntimeError(f"Transkription fehlgeschlagen: {e}") from e

        segments = result.get("segments", []) or []
        for seg in segments:
            self.on_segment(seg)

        self.on_status("WhisperX: Transkription fertig.")
        return result
# app/live_transcriber.py
import threading
import queue
import time
import tempfile
import wave
from app.audio_capture import MicrophoneCapture
from app.stt_whisperx import WhisperXConfig, WhisperXTranscriber

class LiveTranscriber:
    """
    Kontinuierliche Transkription von Mikrofon-Audio.
    Sammelt 10-Sekunden-Chunks und transkribiert überlappend.
    """
    def __init__(self, on_text, on_status, config: WhisperXConfig):
        self.on_text = on_text
        self.on_status = on_status
        self.config = config
        self._running = False
        self._thread = None
        self._mic = None
        
    def start(self):
        if self._running:
            return
        self._running = True
        self._mic = MicrophoneCapture()
        self._mic.start()
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        self.on_status("Live-Transkription: Läuft...")
        
    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._mic:
            self._mic.stop()
        if self._thread:
            self._thread.join(timeout=2)
        self.on_status("Live-Transkription: Gestoppt.")
        
    def _process_loop(self):
        print("DEBUG: _process_loop gestartet")
        transcriber = WhisperXTranscriber(
            self.config,
            on_status=self.on_status,
            on_segment=lambda seg: self.on_text(seg.get("text", "").strip())
        )
        
        chunk_duration = 10  # Sekunden
        sample_rate = 16000
        chunk_size = sample_rate * chunk_duration * 2  # 2 bytes per sample
        
        buffer = []
        buffer_size = 0
        
        print(f"DEBUG: Warte auf Audio (chunk_size={chunk_size} bytes)")
        
        while self._running:
            try:
                # Audio aus Queue holen
                if not self._mic.audio_queue.empty():
                    data = self._mic.audio_queue.get(timeout=0.1)
                    buffer.append(data)
                    buffer_size += len(data)
                    
                    # Wenn genug Daten: transkribieren
                    if buffer_size >= chunk_size:
                        print("DEBUG: Chunk voll, starte Transkription...")
                        audio_data = b''.join(buffer)
                        
                        # Als temporäre WAV speichern
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                            tmp_path = tmp.name
                            with wave.open(tmp_path, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(sample_rate)
                                wf.writeframes(audio_data)
                        
                        # Transkribieren
                        try:
                            transcriber.transcribe_file(tmp_path)
                        except Exception as e:
                            self.on_status(f"Transkriptionsfehler: {e}")
                        
                        # Buffer leeren (mit 50% Overlap für Kontinuität)
                        overlap_samples = len(buffer) // 2
                        buffer = buffer[overlap_samples:]
                        buffer_size = sum(len(b) for b in buffer)
                        
                else:
                    time.sleep(0.05)
                    
            except Exception as e:
                if self._running:
                    self.on_status(f"Live-Fehler: {e}")
                break
            
# app/audio_capture.py
import pyaudio
import queue
import threading

class MicrophoneCapture:
    """
    Einfacher Mikrofon-Capture mit pyaudio.
    Schreibt PCM-Chunks in eine Queue.
    """
    def __init__(self, sample_rate=16000, chunk_duration_ms=100):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration_ms / 1000)
        self.audio_queue = queue.Queue()
        self._running = False
        self._thread = None
        
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            
    def _capture_loop(self):
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )
        
        while self._running:
            try:
                data = stream.read(self.chunk_size, exception_on_overflow=False)
                self.audio_queue.put(data)
            except Exception as e:
                print(f"Mic capture error: {e}")
                break
                
        stream.stop_stream()
        stream.close()
        p.terminate()

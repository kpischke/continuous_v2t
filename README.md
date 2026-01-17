# continuous_v2t
Continuous voice-to-text pipeline using SIP audio, Linphone SDK and Wispr Flow.

## Scope
- SIP audio capture (Linphone)
- Real-time V2T streaming
- Windows executable
- GUI (PySide6)

## Status
Initial scaffold

## Live Transcription (Stable) no SIP yet

The live transcription pipeline is currently considered **stable**.

**Configuration**
- Engine: WhisperX
- Model: `medium`
- Device: CPU
- Compute type: `int8`
- Window size: 5.0 s
- Overlap: 1.5 s
- Audio rate: 16 kHz (mono)
- VAD: Silero
- Silence gate: RMS + peak (dual threshold)
- Start-gate and flush-gate enabled
- Logging: file-based (stdout/stderr redirected)

**Status**
- Real-time live transcription works reliably
- Edge hallucinations at start/end are suppressed
- Console output is clean; diagnostics go to log files
- UI reduced to essential controls via feature flags

**Documentation**
- Full experimental history and design decisions:
  [`docs/LIVE_TRANSCRIPTION_EXPERIMENTS.md`](docs/LIVE_TRANSCRIPTION_EXPERIMENTS_en.md)
  [`docs/STABLE_LIVE_BASELINE.md`](docs/STABLE_LIVE_BASELINE.md)

Further tuning should be treated as experimental and documented accordingly.

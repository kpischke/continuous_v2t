# Stable Live Transcription Baseline

This document defines the frozen baseline for the live transcription pipeline.

## Purpose
The goal of this baseline is stability and reproducibility.
Parameter changes beyond this point must be treated as experimental.

## Frozen Configuration

- Whisper engine: WhisperX
- Model: medium
- Device: CPU
- Compute type: int8
- Sample rate: 16 kHz
- Channels: mono

### Windowing
- window_seconds: 5.0
- overlap_seconds: 1.5

### Voice Activity / Silence Handling
- VAD backend: Silero
- Silence gate: RMS + peak threshold
- Start-gate: enabled (prevents early hallucinations)
- Flush-gate: stricter thresholds for end-of-stream

### Text Handling
- Sliding-window transcription
- Normalized deduplication of overlapping segments
- No aggressive semantic merging

### Logging
- stdout and stderr redirected to log file
- Console output kept minimal
- Detailed diagnostics available in log files

## Known Characteristics
- Minor wording variations are inherent to ASR models
- Sliding windows may still produce partial overlap phrasing
- These effects are acceptable and expected

## Change Policy
Any of the following changes require:
1. explicit documentation
2. comparison against this baseline
3. a new experimental section in the documentation

- model or compute type
- window / overlap parameters
- VAD or silence thresholds
- deduplication logic
- threading or buffering behavior

This baseline represents the first production-quality live transcription state.

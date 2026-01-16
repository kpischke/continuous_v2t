# Live Transcription – Experiments, Parameters & Design Decisions
> Status: Stable – CPU, WhisperX medium, int8
This document contains the full experimental history, parameter tuning,
and design decisions for the live WhisperX-based transcription pipeline.
## Recommended Defaults (Current)
- model: medium
- device: cpu
- compute_type: int8
- window_seconds: 5.0
- overlap_seconds: 1.5
- silence_rms_threshold: 80
- silence_peak_threshold: enabled
- audio_loading: soundfile (WAV), ffmpeg fallback
**Complete, structured document** created in Canvas that:
* Chronologically maps all **buffer/window/overlap attempts**
* Documents **value changes in tabular form**
* Describes **each functional measure** (VAD, audio loading, silence gate, preload, dedupe, logging) individually
* contains **the respective reasoning** why* something was introduced, changed, or rejected
* deliberately summarizes the **venv/version issue only once** (as requested) without overloading the technical flow

# Live transcription (continuous_v2t)
## 1. Objective
The aim of the work was to achieve **stable, continuous live speech recognition** on a CPU basis, with a high recognition rate, no hallucinations, no word loss, and controlled latency. In addition, debug and status messages should be clearly separated (UI vs. log file) and the pipeline should be maintainable in the long term.

## 2. Initial situation
**Initial architecture:**
* Microphone recording (16 kHz, mono)
* Chunking → Sliding Window (window + overlap)
* WhisperX (CPU, `medium`, `int8`)
* VAD via WhisperX (Silero / Pyannote)
**Initial problems:**
* Live transcription did not work or only worked sporadically
* High word loss
* Repetitions due to overlap
* Hallucinations during silence
* Unstable environment due to inconsistent venv states

## 3. Parameter experiments (chronological)
### 3.1 Buffering / Window / Overlap
| Phase   | window_seconds | overlap_seconds | Effect                                   | Rating |
| ------- | -------------- | --------------- | --------------------------------------- - | --------- |
| Start   | 10.0           | 0.0             | High latency, word loss                | ❌         |
| Test    | 5.0            | 1.5             | Good word recognition, but duplicates       | ⚠️        |
| Test    | 5.0            | 1.0             | Fewer duplicates, more losses         | ⚠️        |
| Test    | 4.0            | 1.2             | Fast, but unstable with long sentences | ⚠️        |
| Current | 5.0            | 1.5             | Best recognition rate                     | ✅         |
**Reason:**
* Overlap is essential to avoid cutting off the ends of sentences.
* Too little overlap leads to word loss.
* Greater overlap increases redundancy → must be deduplicated.

### 3.2 Silence / Audio Level Gate
| Parameter | Values       | Result                            |
| --------- | ----------- | ----------------------------------- |
| RMS < 180 | aggressive   | Many genuine speech windows discarded |
| RMS < 140 | moderate     | Better, but still losses          |
| RMS < 80  | conservative | Speech reliably recognized         |
**Final logic:**
* RMS **and** peak are calculated
* Skip only if *both* are below threshold value
**Reason:**
* RMS alone is too sensitive to quiet speech
* Peak complements impulse/consonant detection

## 4. Function experiments & measures
### 4.1 VAD (Voice Activity Detection)
| Measure         | Status    | Reason                              |
| ---------------- | --------- | ----- ---------------------------------- |
| Pyannote VAD     | rejected | Version incompatibilities, unstable   |
| Silero VAD       | active     | stable, high performance, WhisperX integrated |
| Disable VAD | tested  | massive hallucinations                 |
➡️ **Decision:** Keep Silero VAD

### 4.2 Audio loading
| Variant                | Status        | Reason                     |
| ----------------------- | ------------- | ---------------- -------------- |
| `whisperx.load_audio()` | problematic | Dependency on ffmpeg / PATH |
| `soundfile` for WAV     | active         | stable, ffmpeg-free            |
| Hybrid (fallback)       | active         | WAV → soundfile, otherwise ffmpeg  |
**Reason:**
* Live path guarantees WAV (16 kHz mono)
* Elimination of external process dependencies

### 4.3 Model handling
| Measure  | Result                                    |
| --------- | ---------------- --------------------------- |
| Lazy-load | High latency for the first chunk               |
| Preload   | Stable latency, deterministic behavior |
➡️ **Preload introduced before workers start**

### 4.4 Deduplication
| Approach                      | Result     | Evaluation |
| --------------------------- | ------------ | --------- |
| Text-based equality    | Insufficient | ❌         |
| Prefix comparison            | Word loss | ❌         |
| Time-based segment deduplication | Robust       | ✅         |
**Final approach:**
* Windows receive global time offset
* WhisperX segments are mapped to global timeline
* Segments with `global_end <= last_emitted_end` are discarded
**Reason:**
* Overlapping audio creates legitimate repetitions
* Time-based filtering is semantically correct

## 5. Logging & UX cleanup
### 5.1 Console output
**Before:**
* Deprecation warnings
* Silero INFO/WARNING messages
* Confusing for end users
**After:**
* Console: only ERROR / tracebacks
* Log file (`app.log`, `live_debug.log`): INFO + WARN
**Measures:**
* `logging.captureWarnings(True)`
* Targeted `warnings.filterwarnings(...)`
* Logger level set per module

## 6. Environment/venv issues (summarized)
During the work, problems arose **several times** (repeatedly) due to:
* Destroyed or inconsistent `venv`
* Incompatible combinations of `torch`, `torchaudio`, `pyannote`, `whisperx`
* Incorrect shells with deviating PATH (ffmpeg not visible)
➡️ These problems were **not logical/code-related**, but purely environment-related.
**Countermeasures:**
* Clean reinitialization of venv
* Versions consistently linked to WhisperX
* ffmpeg dependency eliminated in live path

## 7. Current status (reference)
* Live transcription stable
* Very good recognition rate (CPU, `medium`, `int8`)
* No hallucinations during silence
* Overlap duplicates controllable
* Clean logging
**Example (current status):**
> "The fire department reports an accident involving an emergency vehicle in Giesing and a fatal fire in a high-rise building. State and Bavarian news related to Munich focus on upcoming or ongoing changes to rail and public transport, as well as general state politics, among other things."

## 8. Next optional steps
* Fine-tuning of the time-based deduplication threshold
* Optional sentence finalization
* Optional diarization

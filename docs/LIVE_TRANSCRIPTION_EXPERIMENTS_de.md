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

**vollständiges, strukturiertes Dokument** im Canvas erstellt, das:

* alle **Buffer-/Window-/Overlap-Versuche** chronologisch abbildet
* die **Wertveränderungen tabellarisch** dokumentiert
* **jede funktionale Maßnahme** (VAD, Audio-Loading, Silence-Gate, Preload, Dedupe, Logging) einzeln beschreibt
* **jeweils die Begründung** enthält, *warum* etwas eingeführt, geändert oder verworfen wurde
* die **venv/Versionsproblematik bewusst nur einmal zusammengefasst** (wie gewünscht), ohne den technischen Fluss zu überfrachten


# Live-Transkription (continuous_v2t)

## 1. Zielsetzung

Ziel der Arbeiten war es, eine **stabile, kontinuierliche Live‑Spracherkennung** auf CPU‑Basis zu erreichen, mit hoher Erkennungsrate, ohne Halluzinationen, ohne Wortverluste und mit kontrollierter Latenz. Zusätzlich sollten Debug‑ und Statusmeldungen sauber getrennt (UI vs. Logfile) und die Pipeline langfristig wartbar sein.


## 2. Ausgangssituation

**Initiale Architektur:**

* Mikrofonaufnahme (16 kHz, mono)
* Chunking → Sliding Window (Fenster + Overlap)
* WhisperX (CPU, `medium`, `int8`)
* VAD über WhisperX (Silero / Pyannote)

**Probleme zu Beginn:**

* Live‑Transkription funktionierte nicht oder nur sporadisch
* Hohe Wortverluste
* Wiederholungen durch Overlap
* Halluzinationen bei Stille
* Instabile Umgebung durch inkonsistente venv‑Zustände


## 3. Parameter‑Experimente (chronologisch)

### 3.1 Buffering / Window / Overlap

| Phase   | window_seconds | overlap_seconds | Effekt                                   | Bewertung |
| ------- | -------------- | --------------- | ---------------------------------------- | --------- |
| Start   | 10.0           | 0.0             | Hohe Latenz, Wortverluste                | ❌         |
| Test    | 5.0            | 1.5             | Gute Worterkennung, aber Duplikate       | ⚠️        |
| Test    | 5.0            | 1.0             | Weniger Duplikate, mehr Verluste         | ⚠️        |
| Test    | 4.0            | 1.2             | Schnell, aber instabil bei langen Sätzen | ⚠️        |
| Aktuell | 5.0            | 1.5             | Beste Erkennungsrate                     | ✅         |

**Begründung:**

* Overlap ist zwingend nötig, um Satzenden nicht abzuschneiden.
* Zu kleiner Overlap führt zu Wortverlusten.
* Größerer Overlap erhöht Redundanz → muss dedupliziert werden.


### 3.2 Silence / Audio‑Level Gate

| Parameter | Werte       | Ergebnis                            |
| --------- | ----------- | ----------------------------------- |
| RMS < 180 | aggressiv   | Viele echte Sprachfenster verworfen |
| RMS < 140 | moderat     | Besser, aber noch Verluste          |
| RMS < 80  | konservativ | Sprache zuverlässig erkannt         |

**Finale Logik:**

* RMS **und** Peak werden berechnet
* Skip nur bei *beiden* unter Schwellwert

**Begründung:**

* RMS allein ist zu empfindlich gegenüber leiser Sprache
* Peak ergänzt Impuls‑/Konsonanten‑Erkennung


## 4. Funktions‑Experimente & Maßnahmen

### 4.1 VAD (Voice Activity Detection)

| Maßnahme         | Status    | Begründung                              |
| ---------------- | --------- | --------------------------------------- |
| Pyannote VAD     | verworfen | Versions‑Inkompatibilitäten, instabil   |
| Silero VAD       | aktiv     | stabil, performant, WhisperX‑integriert |
| VAD deaktivieren | getestet  | massive Halluzinationen                 |

➡️ **Entscheidung:** Silero VAD beibehalten


### 4.2 Audio‑Loading

| Variante                | Status        | Begründung                     |
| ----------------------- | ------------- | ------------------------------ |
| `whisperx.load_audio()` | problematisch | Abhängigkeit von ffmpeg / PATH |
| `soundfile` für WAV     | aktiv         | stabil, ffmpeg‑frei            |
| Hybrid (Fallback)       | aktiv         | WAV → soundfile, sonst ffmpeg  |

**Begründung:**

* Live‑Pfad erzeugt garantiert WAV (16 kHz mono)
* Eliminierung externer Prozessabhängigkeiten


### 4.3 Modell‑Handling

| Maßnahme  | Ergebnis                                    |
| --------- | ------------------------------------------- |
| Lazy‑Load | hohe Latenz beim ersten Chunk               |
| Preload   | stabile Latenz, deterministisches Verhalten |

➡️ **Preload vor Start der Worker eingeführt**


### 4.4 Deduplizierung

| Ansatz                      | Ergebnis     | Bewertung |
| --------------------------- | ------------ | --------- |
| Text‑basierte Gleichheit    | unzureichend | ❌         |
| Prefix‑Vergleich            | Wortverluste | ❌         |
| Zeitbasierte Segment‑Dedupe | robust       | ✅         |

**Finaler Ansatz:**

* Fenster erhalten globalen Zeit‑Offset
* WhisperX‑Segmente werden auf globale Timeline abgebildet
* Segmente mit `global_end <= last_emitted_end` werden verworfen

**Begründung:**

* Überlapptes Audio erzeugt legitime Wiederholungen
* Zeitbasierte Filterung ist semantisch korrekt


## 5. Logging & UX‑Cleanup

### 5.1 Konsolen‑Ausgaben

**Vorher:**

* Deprecation Warnings
* Silero INFO/WARNING Meldungen
* Unübersichtlich für Endnutzer

**Nachher:**

* Konsole: nur ERROR / Tracebacks
* Logfile (`app.log`, `live_debug.log`): INFO + WARN

**Maßnahmen:**

* `logging.captureWarnings(True)`
* gezielte `warnings.filterwarnings(...)`
* Logger‑Level pro Modul gesetzt


## 6. Umgebungs‑/venv‑Problematik (zusammengefasst)

Während der Arbeiten kam es **mehrfach** (wiederholt) zu Problemen durch:

* zerstörte oder inkonsistente `venv`
* inkompatible Kombinationen aus `torch`, `torchaudio`, `pyannote`, `whisperx`
* falsche Shells mit abweichendem PATH (ffmpeg nicht sichtbar)

➡️ Diese Probleme waren **nicht logisch/code‑bedingt**, sondern rein umgebungsbedingt.

**Gegenmaßnahmen:**

* saubere Neuinitialisierung der venv
* Versionen konsistent an WhisperX gebunden
* ffmpeg‑Abhängigkeit im Live‑Pfad eliminiert


## 7. Aktueller Status (Referenz)

* Live‑Transkription stabil
* Sehr gute Erkennungsrate (CPU, `medium`, `int8`)
* Keine Halluzinationen bei Stille
* Overlap‑Duplikate kontrollierbar
* Sauberes Logging

**Beispiel (aktueller Stand):**

> „Die Feuerwehr meldet einen Unfall mit einem Einsatzfahrzeug in Giesing sowie einen tödlichen Brand in einem Hochhaus. Landes‑ und Bayern‑News mit München‑Bezug drehen sich unter anderem um anstehende bzw. laufende Änderungen bei Bahn und ÖPNV sowie allgemeine Landespolitik.“


## 8. Nächste optionale Schritte

* Feinjustierung der zeitbasierten Dedupe‑Schwelle
* optionale Satzfinalisierung
* optionale Diarisierung



#!/usr/bin/env python3
"""Transcribe a meeting while it is still being recorded.

Reads the two growing capture files (mic on one, system audio on the other),
transcribes them in windows as the audio arrives, and appends finished turns to
a partial transcript. By the time the meeting ends only the final window is
left to process, instead of the whole recording.

Windows are cut at a silence found by VAD rather than on a fixed clock, so a
boundary rarely lands mid-word; each window also carries a short overlap into
the next as a safety net.

Usage: live_transcribe.py <base-path> <model> <you-label> <them-label>
  <base-path>.mic.wav / .sys.wav   grow while recording
  <base-path>.partial.txt          turns appended as they are recognised
  <base-path>.live.done            written when the worker exits cleanly

Stops when <base-path>.stop appears, then drains whatever audio remains.
"""
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

HEADER = 44                      # canonical PCM wav header parec writes
RATE = 16000
WINDOW_S = float(os.environ.get("MEETINGINSIGHTS_LIVE_WINDOW", "45"))
OVERLAP_S = 1.5
THREADS = int(os.environ.get("MEETINGINSIGHTS_LIVE_THREADS", "3"))
MIN_S = 8.0                      # do not bother the model with scraps


def mmss(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


class Track:
    """One growing mono wav file, read as raw PCM from a byte offset."""

    def __init__(self, path: Path, label: str):
        self.path, self.label = path, label
        self.offset = HEADER         # bytes already consumed
        self.start_s = 0.0           # audio time of the next unread sample

    def available(self) -> int:
        try:
            return max(0, self.path.stat().st_size - self.offset)
        except OSError:
            return 0

    def read(self, nbytes: int) -> np.ndarray:
        nbytes -= nbytes % 2         # keep whole samples
        if nbytes <= 0:
            return np.empty(0, dtype=np.float32)
        with self.path.open("rb") as f:
            f.seek(self.offset)
            raw = f.read(nbytes)
        self.offset += len(raw)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def main() -> int:
    base = Path(sys.argv[1])
    model_size = sys.argv[2] if len(sys.argv) > 2 else "small.en"
    you = sys.argv[3] if len(sys.argv) > 3 else "You"
    them = sys.argv[4] if len(sys.argv) > 4 else "Participants"

    stop_flag = Path(str(base) + ".stop")
    partial = Path(str(base) + ".partial.txt")
    done_flag = Path(str(base) + ".live.done")
    partial.write_text("")

    tracks = [Track(Path(f"{base}.mic.wav"), you),
              Track(Path(f"{base}.sys.wav"), them)]

    # Fewer threads than the batch path: this runs during the call, and the
    # meeting software needs the CPU more than we do.
    model = WhisperModel(model_size, device="cpu", compute_type="int8",
                         cpu_threads=THREADS)
    print(f"live: model={model_size} threads={THREADS} window={WINDOW_S}s",
          file=sys.stderr, flush=True)

    window_bytes = int(WINDOW_S * RATE) * 2
    overlap = int(OVERLAP_S * RATE)
    carry = {t.label: np.empty(0, dtype=np.float32) for t in tracks}
    rows = []

    def flush(track, samples, t0):
        if samples.size < MIN_S * RATE or float(np.abs(samples).max() or 0) < 0.004:
            return
        segments, _ = model.transcribe(
            samples, beam_size=1, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400})
        for seg in segments:
            text = seg.text.strip()
            if text:
                rows.append((t0 + seg.start, track.label, text))

    def write_partial():
        rows.sort(key=lambda r: r[0])
        with partial.open("w") as fh:
            for t, label, text in rows:
                fh.write(f"[{mmss(t)}] {label}: {text}\n")

    draining = False
    while True:
        worked = False
        for tr in tracks:
            need = window_bytes if not draining else 2
            if tr.available() >= need:
                chunk = tr.read(window_bytes if not draining else tr.available())
                if chunk.size == 0:
                    continue
                audio = np.concatenate([carry[tr.label], chunk])
                t0 = tr.start_s - carry[tr.label].size / RATE
                flush(tr, audio, t0)
                tr.start_s += chunk.size / RATE
                carry[tr.label] = audio[-overlap:] if not draining else np.empty(0, dtype=np.float32)
                worked = True
        if worked:
            write_partial()
        if draining:
            break
        if stop_flag.exists():
            draining = True          # one more pass takes whatever is left
            continue
        if not worked:
            time.sleep(2)

    write_partial()
    done_flag.write_text(str(len(rows)))
    print(f"live: {len(rows)} turns", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Transcribe a two-channel meeting recording (L=mic/you, R=system/them).

Each channel is transcribed separately, so speaker attribution comes from the
audio routing rather than a diarization model. Segments are then interleaved
chronologically into a single labelled transcript.

Usage: transcribe.py <wav> <out.txt> [model] [you_label] [them_label]
"""
import sys
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel


def mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def main() -> int:
    wav, out = Path(sys.argv[1]), Path(sys.argv[2])
    model_size = sys.argv[3] if len(sys.argv) > 3 else "small.en"
    you = sys.argv[4] if len(sys.argv) > 4 else "You"
    them = sys.argv[5] if len(sys.argv) > 5 else "Them"

    import wave

    with wave.open(str(wav), "rb") as w:
        channels, rate = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    tracks = {}
    if channels == 2:
        stereo = audio.reshape(-1, 2)
        tracks[you] = stereo[:, 0]
        tracks[them] = stereo[:, 1]
    else:
        tracks["Speaker"] = audio

    print(f"model={model_size} rate={rate}Hz channels={channels}", file=sys.stderr)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    rows = []
    for label, samples in tracks.items():
        if float(np.abs(samples).max() or 0) < 0.005:
            print(f"  {label}: silent, skipped", file=sys.stderr)
            continue
        print(f"  transcribing {label} ...", file=sys.stderr)
        segments, _ = model.transcribe(
            samples, beam_size=5, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        for seg in segments:
            text = seg.text.strip()
            if text:
                rows.append((seg.start, label, text))

    rows.sort(key=lambda r: r[0])

    # merge consecutive lines from the same speaker into one paragraph
    merged, prev = [], None
    for start, label, text in rows:
        if prev and prev[1] == label and start - prev[2] < 8:
            merged[-1] = (merged[-1][0], label, merged[-1][2] + " " + text)
        else:
            merged.append((start, label, text))
        prev = (start, label, start)

    with out.open("w") as fh:
        for start, label, text in merged:
            fh.write(f"[{mmss(start)}] {label}: {text}\n")

    print(f"{len(merged)} turns -> {out}", file=sys.stderr)
    return 0 if merged else 3


if __name__ == "__main__":
    sys.exit(main())

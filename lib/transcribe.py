#!/usr/bin/env python3
"""Transcribe a two-channel meeting recording (L=mic/you, R=system/them).

Each channel is transcribed separately, so speaker attribution comes from the
audio routing rather than a diarization model. Segments are then interleaved
chronologically into a single labelled transcript.

Usage: transcribe.py <wav> <out.txt> [model] [you_label] [them_label]
"""
import re
import sys
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel


def channels_duplicated(left, right, rate, window_s=20):
    """True when both channels carry the same signal, merely time-shifted.

    If the microphone and the system monitor end up on the same source, each
    channel transcribes the same speech and every turn gets attributed twice
    under two different names. Labelling that as two speakers is worse than
    admitting there is only one track, so detect it and say so.

    Correlation must be measured at the best lag: the two paths are offset by
    hundreds of milliseconds, and comparing them sample-aligned (or after
    decimating) hides an otherwise perfect match.
    """
    n = min(len(left), len(right))
    if n < rate * 4:
        return False
    mid = n // 2
    half = min(int(rate * window_s), n) // 2
    a = left[mid - half:mid + half].astype(np.float64)
    b = right[mid - half:mid + half].astype(np.float64)
    if a.size == 0 or b.size == 0:
        return False
    a = a - a.mean()
    b = b - b.mean()
    if not np.any(a) or not np.any(b):
        return False
    c = np.correlate(a, b, mode="full")
    lag = int(c.argmax()) - (len(b) - 1)
    if lag >= 0:
        x, y = a[lag:], b[:len(b) - lag]
    else:
        x, y = a[:len(a) + lag], b[-lag:]
    m = min(len(x), len(y))
    if m < rate:
        return False
    r = float(np.corrcoef(x[:m], y[:m])[0, 1])
    return r > 0.98


def transcripts_overlap(rows_a, rows_b, threshold=0.45):
    """True when two channels transcribed largely the same speech.

    Catches acoustic bleed: with speakers rather than headphones the microphone
    hears the far end too, so both channels carry the same words. The waveforms
    differ (room reverb, mic response) so a correlation test passes them, but
    the text does not lie. Compared on word 4-grams, which survive the small
    recognition differences between the two paths.
    """
    def grams(rows):
        # normalise punctuation: the two paths transcribe the same speech with
        # different commas and capitals, which would otherwise break every match
        text = re.sub(r"[^a-z0-9 ]", " ", " ".join(t.lower() for _, t in rows))
        words = text.split()
        return {tuple(words[i:i + 4]) for i in range(len(words) - 3)}

    ga, gb = grams(rows_a), grams(rows_b)
    if len(ga) < 12 or len(gb) < 12:
        return False
    return len(ga & gb) / min(len(ga), len(gb)) >= threshold


def mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def check_only(path: Path) -> int:
    """--check-channels: exit 0 when the two channels differ, 1 when duplicated."""
    import wave as _w
    with _w.open(str(path), "rb") as w:
        ch, rate = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if ch != 2:
        return 0
    st = (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0).reshape(-1, 2)
    return 1 if channels_duplicated(st[:, 0], st[:, 1], rate) else 0


def levels(path: Path) -> int:
    """--levels: describe how loud each channel was, in plain language."""
    import wave as _w
    with _w.open(str(path), "rb") as w:
        ch, rate = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    chans = ([("Microphone", a[0::2]), ("System audio", a[1::2])] if ch == 2
             else [("Audio", a)])
    for name, data in chans:
        if data.size == 0:
            continue
        rms = float(np.sqrt((data ** 2).mean()))
        if rms < 0.0005:
            print(f"  {name}: silent — nothing was captured on this input.")
        elif rms < 0.005:
            print(f"  {name}: very quiet (rms {rms:.4f}) — check the input level.")
        else:
            print(f"  {name}: audible (rms {rms:.4f}).")
    return 0


def main() -> int:
    if sys.argv[1] == "--levels":
        return levels(Path(sys.argv[2]))
    if sys.argv[1] == "--check-channels":
        return check_only(Path(sys.argv[2]))
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
        if channels_duplicated(stereo[:, 0], stereo[:, 1], rate):
            print("WARNING: both channels contain the same audio — the microphone "
                  "and system capture resolved to one source. Transcribing as a "
                  "single track; speakers cannot be told apart.", file=sys.stderr)
            tracks["Speaker"] = stereo[:, 0]
        else:
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

    # If both channels heard the same speech, keep the louder one rather than
    # reporting every sentence twice under two names.
    if len(tracks) == 2:
        labels = list(tracks)
        per = {lab: [(st, tx) for st, l, tx in rows if l == lab] for lab in labels}
        if transcripts_overlap(per[labels[0]], per[labels[1]]):
            # Keep whichever channel recognised more speech, not the louder one:
            # the microphone is usually louder but is a room re-recording of the
            # far end, while the system monitor is a clean digital copy.
            best = max(labels, key=lambda l: sum(len(tx.split()) for _, tx in per[l]))
            print(f"WARNING: both channels transcribed the same speech — the microphone "
                  f"is picking up the other side (speakers rather than headphones). "
                  f"Keeping the '{best}' channel only; speakers cannot be told apart.",
                  file=sys.stderr)
            rows = [(st, "Speaker", tx) for st, lab, tx in rows if lab == best]

    rows.sort(key=lambda r: r[0])

    # merge consecutive lines from the same speaker into one paragraph
    merged, prev = [], None
    for start, label, text in rows:
        # Merge consecutive segments from one speaker into a readable turn, but
        # stop at roughly a paragraph. Without a cap a single-speaker recording
        # collapses into one enormous turn, which reads badly and makes every
        # downstream action item a wall of text.
        if (prev and prev[1] == label and start - prev[2] < 8
                and len(merged[-1][2].split()) < 60):
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

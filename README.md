# MeetingInsights — AI Note Taker

Record a meeting, get structured minutes. Everything runs on your machine — no
account, no API key, no upload.

Linux desktop app (GTK) plus a CLI. Audio is captured with PulseAudio,
transcribed by [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
summarised by a small local model through [llama.cpp](https://github.com/ggml-org/llama.cpp).

## Why it attributes speakers correctly

Your microphone is recorded to the **left** channel and everything you hear to
the **right**. Each channel is transcribed separately, so "who said what" comes
from how the audio was routed rather than from a diarization model guessing at
voices. Action-item owners are then read off those labels — the language model
is never asked who committed to what, so it cannot invent a participant.

## What the model actually does

Structure, classification and attribution are handled in code:

| Part of the notes | Produced by |
|---|---|
| Speaker labels | audio routing |
| Questions, decisions, action items | pattern rules over the transcript |
| Action-item owners | speaker labels |
| Prose summaries | the local language model |

This is why a 0.5B model is enough. In testing, moving classification out of the
model and into rules fixed every misclassification and cut runtime by ~75%.

## Requirements

- Linux with PulseAudio or PipeWire, `ffmpeg`, Python 3.9+
- ~1.5 GB disk for the models, ~650 MB RAM while processing
- No GPU needed

## Install

```sh
./install.sh          # installs to ~/.local, creates the Python environment
meetinginsights setup       # fetches the speech and language models (~1.5 GB, once)
```

## Use

```sh
meetinginsights start "Sprint planning"
meetinginsights stop                      # transcribes, then writes notes
meetinginsights status
meetinginsights notes <transcript.txt>    # regenerate notes from a transcript
```

Or launch **MeetingInsights** from your applications menu.

Notes land in `~/MeetingNotes/`, recordings and transcripts in
`~/.local/share/meetinginsights/rec/`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MEETINGINSIGHTS_OUT` | `~/MeetingNotes` | where notes are written |
| `MEETINGINSIGHTS_WHISPER_MODEL` | `small.en` | try `medium.en` for better accuracy |
| `MEETINGINSIGHTS_LLM_MODEL` | Qwen2.5-0.5B Q4_K_M | any GGUF chat model |
| `MEETINGINSIGHTS_YOU` / `MEETINGINSIGHTS_THEM` | `You` / `Participants` | channel labels |

## Recording consent

Recording a conversation without telling the other participants is illegal in
many places. `meetinginsights start` prints a reminder; please honour it.

## Licence

MIT — see `LICENSE`.

## Links

- Source: https://github.com/techbysaurabh/meetinginsights-linux
- The iOS sibling, *MeetingInsights: AI Minutes*, is on the App Store — this is
  the Linux counterpart and shares its icon and design language.

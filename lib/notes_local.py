#!/usr/bin/env python3
"""Offline meeting-notes writer backed by a small local GGUF model.

Runs entirely on the machine — no network, no API, no account.

Design: the model is never asked to author the document. Python owns the
structure and all attribution; the model only does two narrow jobs it is
reliable at even at 0.5B — classifying a turn, and paraphrasing a chunk.
Action-item owners come from the transcript's speaker labels (which are exact,
since they come from audio routing), never from the model, so the usual
small-model failure of inventing attendees cannot occur.

Usage: notes_local.py <transcript.txt> <out.md> <model.gguf> [title] [minutes]
"""
import os
import re
import subprocess
import sys
from pathlib import Path

LLAMA_DIR = Path(os.environ.get(
    "MEETINGINSIGHTS_LLAMA_DIR", Path.home() / ".local/share/meetinginsights/llama"))
LLAMA_BIN = LLAMA_DIR / "llama"
THREADS = "6"

CLASSES = ("action", "decision", "question", "discussion")


def run_model(model: str, system: str, user: str, n_predict: int, grammar: str | None = None) -> str:
    """One-shot generation via `llama cli -st`. Returns just the model's reply."""
    cmd = [str(LLAMA_BIN), "cli", "--model", model, "-sys", system, "-p", user,
           "-st", "-n", str(n_predict), "-t", THREADS, "--temp", "0.2",
           "-c", "4096", "--no-warmup"]
    if grammar:
        cmd += ["--grammar", grammar]
    env = {"LD_LIBRARY_PATH": str(LLAMA_DIR), "PATH": "/usr/bin:/bin",
           "HOME": str(Path.home()), "TERM": "dumb"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        return ""

    # the reply sits between the echoed prompt line ("> ...") and the stats line
    lines, reply, seen = r.stdout.splitlines(), [], False
    for line in lines:
        if not seen:
            if line.startswith("> "):
                seen = True
            continue
        if line.startswith("[ Prompt:") or line.strip() == "Exiting...":
            break
        reply.append(line)
    return "\n".join(reply).strip()


def chat(model: str, system: str, user: str, n_predict: int, grammar=None) -> str:
    return run_model(model, system, user, n_predict, grammar)


def parse_turns(text: str):
    turns = []
    for line in text.splitlines():
        m = re.match(r"\[(\d{2}:\d{2})\]\s+([^:]+):\s*(.*)", line.strip())
        if m:
            turns.append({"t": m.group(1), "speaker": m.group(2).strip(), "text": m.group(3).strip()})
    return turns


def chunk(turns, max_chars=500):
    out, cur, n = [], [], 0
    for t in turns:
        if n + len(t["text"]) > max_chars and cur:
            out.append(cur); cur, n = [], 0
        cur.append(t); n += len(t["text"])
    if cur:
        out.append(cur)
    return out


QUESTION_RE = re.compile(r"\?\s*$")
COMMIT_RE = re.compile(
    r"\b(i will|i\'ll|i am going to|i\'m going to|i can|let me|i shall)\b", re.I)
REQUEST_RE = re.compile(r"\b(can you|could you|please|would you|can we|make sure)\b", re.I)
DECISION_RE = re.compile(
    r"\b(let us|let\'s|we should|agreed|we will go with|decided|hold off|"
    r"we are going to|going with|sign off|approved)\b", re.I)
FILLER_RE = re.compile(
    r"^(hi|hello|morning|good morning|thanks|thank you|ok|okay|right|good|yes|no)\b[\s,.!]*$",
    re.I)


def classify(turn, prev_speaker=None) -> str:
    """Rule-based classification.

    Deliberately not a model call: on this task simple patterns beat a small
    LLM outright (a 0.5B model labelled greetings and questions as actions),
    and rules never hallucinate. The model's judgement is reserved for prose.
    """
    text = turn["text"].strip()
    if FILLER_RE.match(text) or len(text.split()) < 4:
        return "discussion"
    if QUESTION_RE.search(text):
        # "can you raise a ticket?" is a request for action, not an open question
        return "action" if REQUEST_RE.search(text) else "question"
    if COMMIT_RE.search(text):
        return "action"
    if REQUEST_RE.search(text):
        return "action"
    if DECISION_RE.search(text):
        return "decision"
    return "discussion"


def summarize_chunk(model, turns) -> str:
    # single line: the CLI echoes the prompt, and a multi-line echo would be
    # mistaken for the model's reply by the output parser
    body = "  ".join(f'{t["speaker"]} said: {t["text"]}' for t in turns)
    sys_p = ("Summarize this part of a meeting in ONE factual sentence. "
             "Use only what is stated. Do not add names or facts that are not present.")
    s = chat(model, sys_p, body, 90).strip()
    s = s.split("\n")[0].strip().strip('"')
    return s if len(s) > 15 else ""


def main() -> int:
    transcript, out_path, model = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    title = sys.argv[4] if len(sys.argv) > 4 else "Meeting"
    minutes = sys.argv[5] if len(sys.argv) > 5 else "?"

    if not LLAMA_BIN.exists():
        print("llama binary not found", file=sys.stderr)
        return 2

    turns = parse_turns(transcript.read_text())
    if not turns:
        print("no turns parsed", file=sys.stderr)
        return 3

    speakers = sorted({t["speaker"] for t in turns})
    chunks = chunk(turns)
    print(f"{len(turns)} turns, {len(chunks)} chunks, model={Path(model).name}", file=sys.stderr)

    summaries = []
    for i, ch in enumerate(chunks, 1):
        print(f"  summarizing chunk {i}/{len(chunks)}", file=sys.stderr)
        s = summarize_chunk(model, ch)
        if s:
            summaries.append((ch[0]["t"], s))

    buckets = {c: [] for c in CLASSES}
    substantive = [t for t in turns if len(t["text"].split()) >= 4]
    for i, t in enumerate(substantive, 1):

        buckets[classify(t)].append(t)

    L = []
    L.append(f"# {title}\n")
    L.append(f"*{minutes} minutes · {len(turns)} turns · participants: {', '.join(speakers)}*\n")
    L.append("> Notes written offline by a small local model. Structure and speaker "
             "attribution are derived from the transcript itself; only the prose "
             "summaries are model-generated.\n")

    L.append("## Summary\n")
    L.extend(f"- {s}" for _, s in summaries) if summaries else L.append("_Could not summarize._")
    L.append("")

    L.append("## Action Items\n")
    if buckets["action"]:
        L.append("| Owner | Action | Time |")
        L.append("|---|---|---|")
        for t in buckets["action"]:
            L.append(f'| {t["speaker"]} | {t["text"]} | {t["t"]} |')
    else:
        L.append("_None identified._")
    L.append("")

    L.append("## Decisions\n")
    L.extend(f'- **{t["speaker"]}** ({t["t"]}): {t["text"]}' for t in buckets["decision"]) \
        if buckets["decision"] else L.append("_None recorded._")
    L.append("")

    L.append("## Open Questions\n")
    L.extend(f'- **{t["speaker"]}** ({t["t"]}): {t["text"]}' for t in buckets["question"]) \
        if buckets["question"] else L.append("_None raised._")
    L.append("")

    out_path.write_text("\n".join(L) + "\n")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/bin/bash
# Install MeetingInsights into ~/.local (no root required).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/meetinginsights"
DATA="$HOME/.local/share/meetinginsights"

echo "Installing MeetingInsights to $PREFIX"

missing=()
for c in parec ffmpeg ffprobe python3; do command -v "$c" >/dev/null || missing+=("$c"); done
python3 -c "import gi" 2>/dev/null || missing+=("python3-gi")
if ((${#missing[@]})); then
    echo "Missing dependencies: ${missing[*]}" >&2
    echo "On Debian/Ubuntu:" >&2
    echo "  sudo apt install pulseaudio-utils ffmpeg python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0 gir1.2-notify-0.7 python3-venv" >&2
    exit 1
fi

mkdir -p "$SHARE" "$PREFIX/bin" "$PREFIX/share/applications" "$DATA/models"
cp -r "$SRC/lib" "$SRC/gui" "$SHARE/"
install -m755 "$SRC/bin/meetinginsights" "$PREFIX/bin/meetinginsights"

cat > "$PREFIX/bin/meetinginsights-gui" <<GUI
#!/bin/bash
exec python3 "$SHARE/gui/meetinginsights_gui.py" "\$@"
GUI
chmod +x "$PREFIX/bin/meetinginsights-gui"

for sz in 16 22 24 32 48 64 128 256 512; do
    d="$PREFIX/share/icons/hicolor/${sz}x${sz}/apps"
    mkdir -p "$d"
    install -m644 "$SRC/icons/${sz}x${sz}/meetinginsights.png" "$d/meetinginsights.png" 2>/dev/null || true
done
gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true

sed "s|^Exec=.*|Exec=$PREFIX/bin/meetinginsights-gui|" "$SRC/meetinginsights.desktop" \
    > "$PREFIX/share/applications/meetinginsights.desktop"
update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

if [[ ! -x "$DATA/venv/bin/python" ]]; then
    echo "Creating the Python environment (this takes a minute)..."
    python3 -m venv "$DATA/venv"
    "$DATA/venv/bin/pip" install -q --upgrade pip
    "$DATA/venv/bin/pip" install -q faster-whisper
fi

if [[ ! -x "$DATA/llama/llama" ]]; then
    echo
    echo "The llama.cpp runtime is not installed yet."
    echo "Build it once with:"
    echo "  git clone --depth 1 https://github.com/ggml-org/llama.cpp"
    echo "  cd llama.cpp && cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF"
    echo "  cmake --build build -j --target llama-app"
    echo "  mkdir -p $DATA/llama && cp build/bin/llama build/bin/*.so* $DATA/llama/"
fi

echo
echo "Installed. Next:"
echo "  meetinginsights setup     # download the models (~1.5 GB, one time)"
echo "  meetinginsights start \"My meeting\""
echo
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: add $PREFIX/bin to your PATH." ;; esac

#!/bin/bash
# Build the MeetingInsights .deb.
#
# Requires a compiled llama.cpp runtime; point LLAMA_DIR at a directory holding
# the `llama` binary and its .so files (see README for the build commands).
# The runtime is bundled so users never have to compile anything — upstream's
# prebuilt binaries need glibc 2.34, which excludes Ubuntu 20.04 and 22.04.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="${VER:-0.1.0}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/.local/share/meetinginsights/llama}"
OUT="${OUT:-$SRC/dist}"
B="$OUT/meetinginsights_${VER}_amd64"

[[ -x "$LLAMA_DIR/llama" ]] || { echo "no llama runtime at $LLAMA_DIR" >&2; exit 1; }
command -v fakeroot >/dev/null || { echo "need fakeroot" >&2; exit 1; }

rm -rf "$B"; mkdir -p "$B"/{DEBIAN,usr/bin,usr/share/meetinginsights,usr/lib/meetinginsights/llama,usr/share/applications,usr/share/doc/meetinginsights,usr/share/man/man1}

cp -r "$SRC/lib" "$SRC/gui" "$B/usr/share/meetinginsights/"
# bytecode from a local test run must not ship in the package
find "$B/usr/share/meetinginsights" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
find "$B/usr/share/meetinginsights" -name '*.py[co]' -delete 2>/dev/null || true
install -m755 "$SRC/bin/meetinginsights" "$B/usr/bin/meetinginsights"
printf '#!/bin/bash\nexec python3 /usr/share/meetinginsights/gui/meetinginsights_gui.py "$@"\n' \
    > "$B/usr/bin/meetinginsights-gui"

install -m755 "$LLAMA_DIR/llama" "$B/usr/lib/meetinginsights/llama/llama"
for so in "$LLAMA_DIR"/*.so*; do install -m644 "$so" "$B/usr/lib/meetinginsights/llama/"; done

# strip build-machine rpaths and debug symbols: lintian errors otherwise, and
# a baked-in rpath would send the loader looking in the build tree at runtime
if command -v patchelf >/dev/null; then
    for f in "$B"/usr/lib/meetinginsights/llama/*; do patchelf --remove-rpath "$f" 2>/dev/null || true; done
elif command -v pip3 >/dev/null && pip3 install --quiet --user patchelf 2>/dev/null \
     && [ -x "$HOME/.local/bin/patchelf" ]; then
    for f in "$B"/usr/lib/meetinginsights/llama/*; do "$HOME/.local/bin/patchelf" --remove-rpath "$f" 2>/dev/null || true; done
else
    echo "warning: patchelf unavailable; verify with 'lintian' that no rpath survived" >&2
fi
strip --strip-unneeded "$B"/usr/lib/meetinginsights/llama/* 2>/dev/null || true

for sz in 16 22 24 32 48 64 128 256 512; do
    d="$B/usr/share/icons/hicolor/${sz}x${sz}/apps"; mkdir -p "$d"
    install -m644 "$SRC/icons/${sz}x${sz}/meetinginsights.png" "$d/meetinginsights.png"
done
install -m644 "$SRC/meetinginsights.desktop" "$B/usr/share/applications/meetinginsights.desktop"
install -m644 "$SRC/README.md" "$B/usr/share/doc/meetinginsights/README.md"
install -m644 "$SRC/LICENSE"  "$B/usr/share/doc/meetinginsights/copyright"
gzip -9nc "$SRC/packaging/meetinginsights.1" > "$B/usr/share/man/man1/meetinginsights.1.gz"
echo ".so man1/meetinginsights.1" | gzip -9n > "$B/usr/share/man/man1/meetinginsights-gui.1.gz"
printf 'meetinginsights (%s) stable; urgency=low\n\n  * Release %s.\n\n -- techbysaurabh <hi.techbysaurabh@gmail.com>  %s\n' \
    "$VER" "$VER" "$(date -R)" | gzip -9n > "$B/usr/share/doc/meetinginsights/changelog.gz"

install -m755 "$SRC/packaging/debian/postinst" "$B/DEBIAN/postinst"
install -m755 "$SRC/packaging/debian/postrm"   "$B/DEBIAN/postrm"

find "$B" -path "*/DEBIAN" -prune -o -type d -print0 | xargs -0 chmod 755
find "$B" -path "*/DEBIAN" -prune -o -type f -print0 | xargs -0 chmod 644
chmod 755 "$B/usr/bin/meetinginsights" "$B/usr/bin/meetinginsights-gui" \
          "$B/usr/lib/meetinginsights/llama/llama" "$B/DEBIAN/postinst" "$B/DEBIAN/postrm"

sed -e "s|^Version:.*|Version: $VER|" \
    -e "s|^Installed-Size:.*|Installed-Size: $(du -sk "$B" | cut -f1)|" \
    "$SRC/packaging/debian/control" > "$B/DEBIAN/control"

fakeroot dpkg-deb --build -Zxz "$B"
command -v lintian >/dev/null && lintian "$B.deb" || true
echo "built: $B.deb"

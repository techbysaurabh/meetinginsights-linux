#!/bin/bash
# Publish a .deb to the techbysaurabh apt repository.
#
#   packaging/publish-apt.sh dist/meetinginsights_0.1.5_amd64.deb
#
# Clones the repo, drops the package in, regenerates and re-signs the index,
# and pushes. GitHub Pages serves it within a minute or so.
#
# Needs: the signing key at ~/.config/oss-release/keys/apt-signing-key.asc and
# GH_TOKEN from ~/.config/oss-release/credentials.env.
set -euo pipefail

DEB="${1:?usage: publish-apt.sh <package.deb>}"
[[ -f "$DEB" ]] || { echo "no such file: $DEB" >&2; exit 1; }

CRED="$HOME/.config/oss-release/credentials.env"
KEY="$HOME/.config/oss-release/keys/apt-signing-key.asc"
[[ -f "$CRED" ]] || { echo "missing $CRED" >&2; exit 1; }
[[ -f "$KEY"  ]] || { echo "missing signing key $KEY" >&2; exit 1; }
set -a; . "$CRED"; set +a
: "${GH_TOKEN:?GH_TOKEN not set}"
: "${APT_SIGNING_KEY:?APT_SIGNING_KEY not set}"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
export GNUPGHOME="$WORK/gnupg"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
gpg --batch --quiet --import "$KEY"

AUTH="Authorization: Basic $(printf 'techbysaurabh:%s' "$GH_TOKEN" | base64 -w0)"
git -c http.extraHeader="$AUTH" clone --quiet --depth 1 \
    https://github.com/techbysaurabh/apt.git "$WORK/repo"
cd "$WORK/repo"
git config user.name techbysaurabh
git config user.email hi.techbysaurabh@gmail.com

# keep one version per package name so the pool does not grow without bound
NAME=$(dpkg-deb -f "$DEB" Package)
VER=$(dpkg-deb -f "$DEB" Version)
rm -f "pool/main/${NAME}_"*.deb
cp "$DEB" pool/main/

dpkg-scanpackages --arch amd64 pool/ > dists/stable/main/binary-amd64/Packages 2>/dev/null
gzip -9kf dists/stable/main/binary-amd64/Packages

cat > "$WORK/aptftp.conf" <<'EOF'
APT::FTPArchive::Release {
  Origin "techbysaurabh";
  Label "MeetingInsights";
  Suite "stable";
  Codename "stable";
  Architectures "amd64";
  Components "main";
  Description "MeetingInsights — on-device meeting recorder for Linux";
};
EOF
apt-ftparchive -c "$WORK/aptftp.conf" release dists/stable > dists/stable/Release
rm -f dists/stable/InRelease dists/stable/Release.gpg
gpg --batch --yes --default-key "$APT_SIGNING_KEY" --clearsign -o dists/stable/InRelease dists/stable/Release
gpg --batch --yes --default-key "$APT_SIGNING_KEY" -abs -o dists/stable/Release.gpg dists/stable/Release

git add -A
git commit -q -m "$NAME $VER"
git -c http.extraHeader="$AUTH" push --quiet origin main
echo "published $NAME $VER — live at https://techbysaurabh.github.io/apt within a minute"

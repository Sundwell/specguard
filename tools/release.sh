#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
dest=${SPECGUARD_RELEASE_DIR:-$HOME/.local/share/specguard-release}
parts=(.claude-plugin hooks scripts agents skills phrases)
cd "$repo"
if [ -n "$(git status --porcelain -- "${parts[@]}")" ]; then
  echo "specguard release: plugin files have uncommitted changes, commit them first" >&2
  exit 1
fi
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests >/dev/null 2>&1 || { echo "specguard release: tests are red" >&2; exit 1; }
tmp=$(mktemp -d)
git archive HEAD "${parts[@]}" | tar -x -C "$tmp"
mkdir -p "$(dirname "$dest")"
rm -rf "$dest"
mv "$tmp" "$dest"
chmod -R u+rwX,go+rX "$dest"
cd "$HOME"
clean_env=$(env | cut -d= -f1 | grep '^CLAUDE' | sed 's/^/-u /' || true)
env $clean_env claude plugin marketplace update specguard
env $clean_env claude plugin update specguard@specguard --scope user
echo "specguard release: $(git -C "$repo" rev-parse --short HEAD) is live in $dest, restart open sessions with claude --continue"

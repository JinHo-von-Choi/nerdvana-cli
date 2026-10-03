#!/usr/bin/env bash
# Release a version in the only order that cannot publish a broken tag.
#
# Usage: scripts/release.sh X.Y.Z
#
# 1. the working tree must be clean and CHANGELOG.md must have entries under [Unreleased]
# 2. scripts/preflight.sh release passes on the commit as it stands (CI conditions, clean checkout)
# 3. the version is bumped everywhere, committed, and preflight quick passes again
# 4. main is pushed WITHOUT the tag, and the script waits until GitHub's checks on that commit are green
# 5. only then the tag is created and pushed, and the script waits for the Release workflow
# Any failure stops the script before the next step; nothing is published by a step that follows a failure.
set -euo pipefail

version="${1:-}"
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "usage: $0 X.Y.Z" >&2
    exit 2
fi
root="$(git rev-parse --show-toplevel)"
cd "$root"

[ -z "$(git status --porcelain)" ] || { echo "release: the working tree is not clean" >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "release: not on main" >&2; exit 1; }
git rev-parse "v$version" >/dev/null 2>&1 && { echo "release: tag v$version already exists" >&2; exit 1; }
python3 - <<PY
import re, sys
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(r"## \[Unreleased\]\n(.*?)\n## \[", text, re.S)
if not m or not m.group(1).strip():
    sys.exit("release: CHANGELOG.md has nothing under [Unreleased]")
PY

echo "== release: preflight on the current commit"
scripts/preflight.sh release

echo "== release: bump to $version"
today="$(date +%F)"
current="$(python3 scripts/release_notes.py version)"
sed -i "s/^version = \"$current\"/version = \"$version\"/" pyproject.toml
sed -i "s/^__version__ = \"$current\"/__version__ = \"$version\"/" nerdvana_cli/__init__.py
python3 - <<PY
import re
from pathlib import Path
lock = Path("uv.lock"); text = lock.read_text()
lock.write_text(re.sub(r'(name = "nerdvana-cli"\nversion = ")[^"]+(")', r'\g<1>$version\g<2>', text, count=1))
change = Path("CHANGELOG.md"); body = change.read_text(encoding="utf-8")
change.write_text(body.replace("## [Unreleased]\n", "## [Unreleased]\n\n## [$version] - $today\n", 1), encoding="utf-8")
PY
python3 scripts/sync_version_badges.py
sed -i "s/판올림 $current/판올림 $version/" README.ko.md
python3 scripts/release_notes.py check-tag "v$version"
git add -A
git commit -q -m "release: $version"

echo "== release: preflight quick on the release commit"
scripts/preflight.sh quick

echo "== release: push main without the tag and wait for the checks"
git push origin main
sha="$(git rev-parse HEAD)"
for attempt in $(seq 1 90); do
    sleep 20
    summary="$(gh run list --commit "$sha" --json name,status,conclusion -q '.[] | select(.name != "Release") | .name + ":" + .status + ":" + (.conclusion // "")')"
    if echo "$summary" | grep -q ":completed:failure\|:completed:cancelled"; then
        echo "release: a check failed on $sha, the tag was NOT created:" >&2
        echo "$summary" >&2
        exit 1
    fi
    if [ -n "$summary" ] && ! echo "$summary" | grep -qv ":completed:success"; then
        break
    fi
done
echo "$summary"
echo "$summary" | grep -qv ":completed:success" && { echo "release: checks did not finish green in time; the tag was NOT created" >&2; exit 1; }

echo "== release: tag and publish"
git tag -a "v$version" -m "nerdvana-cli $version"
git push origin "v$version"
for attempt in $(seq 1 30); do
    sleep 20
    state="$(gh run list --commit "$sha" --workflow Release --json status,conclusion -q '.[0].status + ":" + (.[0].conclusion // "")' 2>/dev/null || true)"
    case "$state" in completed:success) echo "RELEASE OK v$version"; exit 0 ;; completed:*) echo "release: workflow ended as $state" >&2; exit 1 ;; esac
done
echo "release: the Release workflow did not finish in time" >&2
exit 1

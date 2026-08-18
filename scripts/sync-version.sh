#!/usr/bin/env bash
# Sync all manifest version fields to the version derived from the current
# git tag ($GITHUB_REF_NAME on GitHub Actions, e.g. "v0.1.5" → "0.1.5").
#
# Called by .github/workflows/publish.yml before each publish step. Changes
# are in-CI only — never committed back to the repo.
#
# Manifests updated:
#   - pyproject.toml          (PyPI package version)
#   - package.json            (npm root package)
#   - package-lock.json       (kept in lockstep so `npm publish` doesn't warn)
#   - openclaw/package.json   (OpenClaw npm package version)
#   - dsh/package.json        (dsh npm package version)
#
# Idempotent: running twice with the same tag is a no-op.
set -euo pipefail

TAG="${1:-${GITHUB_REF_NAME:-}}"
if [[ -z "$TAG" ]]; then
  echo "usage: $0 <tag>   (or set GITHUB_REF_NAME)" >&2
  exit 1
fi
VERSION="${TAG#v}"

# Basic shape check — semver-ish, e.g. 0.1.5 or 1.2.3-rc.1
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$ ]]; then
  echo "sync-version: unexpected tag shape '$TAG' → version '$VERSION'" >&2
  exit 1
fi

echo "sync-version: setting version to $VERSION (from tag $TAG)"

# pyproject.toml — the only `version = "..."` at file scope
python3 - "$VERSION" <<'PY'
import re, sys, pathlib
version = sys.argv[1]
p = pathlib.Path("pyproject.toml")
text = p.read_text()
new, n = re.subn(r'^version = "[^"]+"', f'version = "{version}"', text, count=1, flags=re.M)
if n != 1:
    sys.exit(f"pyproject.toml: expected one version = line, got {n}")
p.write_text(new)
print(f"  pyproject.toml: {n} substitution(s)")
PY

# npm root and host sub-packages
# `npm version` refuses in a workspace unless we bypass with --no-git-tag-version
# --allow-same-version keeps it idempotent when the tag already matches.
for dir in "." "openclaw" "dsh"; do
  if [[ -f "$dir/package.json" ]]; then
    (cd "$dir" && npm version "$VERSION" --no-git-tag-version --allow-same-version) >/dev/null
    echo "  $dir/package.json: bumped"
  fi
done

echo "sync-version: done"

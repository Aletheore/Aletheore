#!/bin/bash
# Downloads the 20 real repos this validation run scans. First 11 are
# pinned at the same commits already vetted in
# pr-review-benchmark/cases/*/repo.txt; the next 9 were added in round 2
# to cover ecosystems/repo shapes round 1 didn't touch (Rust, Ruby, PHP,
# .NET, Gradle/Kotlin, Swift, plus 3 large diverse repos for deeper
# secrets-FP-rate signal) - pinned at each repo's default-branch HEAD as
# of 2026-09-03 (via `git ls-remote <url> HEAD`), not independently
# re-vetted the way the first 11 were.
# Run this from an empty scratch directory (NOT inside this git repo -
# these are third-party sources, never checked in here), then run
# run_real_repos.py from that same directory.
set -euo pipefail

declare -a repos=(
  "flask|pallets/flask|1d5abfadd7132c9a78e14e5ba6c07aed47115280"
  "requests|psf/requests|3ff3ff21dd45957c9e143cd500291959bb15f690"
  "click|pallets/click|bec59289d8cf9b9b4010642b2fee483e5f8eeefc"
  "express|expressjs/express|18e5985b8a9d5e8423db0a9121f22bdaecd5b120"
  "lodash|lodash/lodash|1fc456c5e4cf43be425f837ad5595747811f7b2d"
  "axios|axios/axios|e566ea39a22e55a05073b035c51852b4863de012"
  "cobra|spf13/cobra|746ef07158728502482cea9f880a6f4b21ef29a9"
  "gin|gin-gonic/gin|c3d5a28ed6d3849da820195b6774d212bcc038a9"
  "gorilla-mux|gorilla/mux|2b030fc311d07b8c5950807800b03b2d32a7142c"
  "gson|google/gson|5e277e42786ab6441b7dd6b1a8c34d545d132307"
  "junit4|junit-team/junit4|f8ee412316b1a94d3dc35498359cc2f0ca273216"
  "clap|clap-rs/clap|af3044228bd76a87a3f46a3c7c3343563c059527"
  "sinatra|sinatra/sinatra|cb22afd7902b566b6eaba6c4ea89739494a65d12"
  "laravel|laravel/laravel|aa0cf127fc365a56ee016867144ddffabc2290ae"
  "restsharp|restsharp/RestSharp|64ee12995b8d7409a5863da7b71cddfa526ffb7a"
  "okhttp|square/okhttp|2ea8dc55a234cea410b2e4f26b36cb367cc98f4a"
  "penny-bot|vapor/penny-bot|e3b999b56d5350a9802c5379aeed13729f318b18"
  "django|django/django|5180f82f48b589f93cddc7be7896f654a9aec1ad"
  "client-go|kubernetes/client-go|12ffdc22a6632c3bd10b0507b84472689b812903"
  "react|facebook/react|d9f4e76bd6582ef86048fefcedda9d5b041ae62f"
)

# Flash Review finding on this PR: a directory existing was previously
# sufficient to skip re-fetching it, with no check that its content
# actually matched the pinned SHA above - a stale directory from a
# partial prior run, or a manual test, would silently get scanned and
# reported on as if it were the declared, pinned commit. Each successful
# extraction now writes a marker recording exactly which SHA it fetched;
# skip only fires when that marker matches the SHA this run asked for.
for entry in "${repos[@]}"; do
  IFS='|' read -r name org_repo sha <<< "$entry"
  marker="$name/.fetched_sha"
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$sha" ]; then
    echo "skip $name (already at $sha)"
    continue
  fi
  if [ -d "$name" ]; then
    echo "$name exists but not at $sha - re-fetching"
    rm -rf "$name"
  fi
  echo "fetching $name ($org_repo @ $sha)"
  curl -sL --max-time 60 "https://github.com/$org_repo/archive/$sha.tar.gz" -o "$name.tar.gz"
  mkdir -p "$name"
  tar -xzf "$name.tar.gz" -C "$name" --strip-components=1
  rm -f "$name.tar.gz"
  echo "$sha" > "$marker"
done

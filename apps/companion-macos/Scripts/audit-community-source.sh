#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOSITORY_DIR="$(cd "$PROJECT_DIR/../.." && pwd)"

fail() {
    echo "community Companion boundary check failed: $1" >&2
    exit 1
}

TRACKED_FILES="$(git -C "$REPOSITORY_DIR" ls-files -- apps/companion-macos)"
if printf '%s\n' "$TRACKED_FILES" | grep -Eiq '(^|/)(\.git|\.build|\.swiftpm|dist)(/|$)|\.(app|zip|dmg|pkg|xcarchive|p12|pfx|pem|key|cer|crt|mobileprovision|provisionprofile|log)$'; then
    fail "a generated binary, build directory, signing credential, profile, or log is tracked"
fi

SCAN_PATHS=(
    "$PROJECT_DIR/Sources"
    "$PROJECT_DIR/Tests"
    "$PROJECT_DIR/Scripts"
    "$PROJECT_DIR/AppBundle"
)

if grep -RInE --include='*.swift' --include='*.sh' --include='*.plist' \
    'https?://(api\.|app\.|updates\.)?sixsentences\.com([/:]|$)' \
    "${SCAN_PATHS[@]}"; then
    fail "a hosted SixSentences network origin remains in community Companion code"
fi

if grep -RInE --include='*.swift' \
    'Host\.current|ProcessInfo\.processInfo\.hostName|device_name' \
    "$PROJECT_DIR/Sources"; then
    fail "host or device identity can enter a request, history, or log"
fi

if grep -RInE --include='*.swift' \
    'feature_not_in_plan|upgrade_required|current workspace plan|contact support|billing portal|checkout|subscription' \
    "$PROJECT_DIR/Sources"; then
    fail "commercial entitlement, upgrade, support, or billing behavior remains"
fi

if grep -RInE --include='*.swift' \
    'receipt\.error|event\.message|state\.errorMessage' \
    "$PROJECT_DIR/Sources"; then
    fail "raw server diagnostics can reach a user-visible surface"
fi

grep -Fq 'SixSentencesCommunityOrigin' \
    "$PROJECT_DIR/Sources/SixSentencesCompanion/Preferences.swift" \
    || fail "the explicit community origin is not read"
grep -Fq 'appendingPathComponent("api"' \
    "$PROJECT_DIR/Sources/SixSentencesCompanion/Preferences.swift" \
    || fail "the /api URL is not derived from the community origin"
grep -Fq 'SIX_COMMUNITY_ORIGIN' "$PROJECT_DIR/Scripts/build-app.sh" \
    || fail "the bundle build does not require a community origin"
grep -Fq '../../docs/assets/sixsentences-mark.svg' "$PROJECT_DIR/Scripts/build-app.sh" \
    || fail "the build does not use the public repository mark"

grep -Rq '/interviews/live' "$PROJECT_DIR/Tests" \
    || fail "live-interview contract tests are missing"
grep -Rq '/companion/paper-chats' "$PROJECT_DIR/Tests" \
    || fail "paper-chat contract tests are missing"
grep -Rq 'testExplicitPurgeDeletesEveryRecoveryFileEvenWhenUnreadable' "$PROJECT_DIR/Tests" \
    || fail "complete local recovery-purge coverage is missing"

grep -Fq 'exact: "2.9.6"' "$PROJECT_DIR/Package.swift" \
    || fail "Sparkle must stay exactly pinned"
grep -Fq '"revision" : "ac2def288cbff5cfc7df3ffef6abdf45b72bcb0a"' \
    "$PROJECT_DIR/Package.resolved" \
    || fail "Package.resolved no longer resolves the reviewed Sparkle revision"

test -f "$PROJECT_DIR/AppBundle/ThirdPartyNotices.txt" \
    || fail "bundled third-party notices are missing"
test -f "$REPOSITORY_DIR/THIRD_PARTY_NOTICES.md" \
    || fail "repository third-party notices are missing"
test -f "$REPOSITORY_DIR/docs/assets/sixsentences-mark.svg" \
    || fail "the public icon source is missing"

bash -n "$PROJECT_DIR/Scripts/build-app.sh"
bash -n "$PROJECT_DIR/Scripts/install-local.sh"
plutil -lint "$PROJECT_DIR/AppBundle/Info.plist" >/dev/null

echo "community Companion boundary check passed"

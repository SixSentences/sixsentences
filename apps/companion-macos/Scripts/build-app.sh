#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_CONFIGURATION="${BUILD_CONFIGURATION:-release}"
APP_NAME="SixSentences Companion"
BUNDLE_DIR="${APP_BUNDLE_DIR:-$PROJECT_DIR/dist/$APP_NAME.app}"
CONTENTS_DIR="$BUNDLE_DIR/Contents"
EXECUTABLE_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
FRAMEWORKS_DIR="$CONTENTS_DIR/Frameworks"
MODULE_CACHE_DIR="${TMPDIR:-/tmp}/sixsentences-companion-module-cache"
ICON_SOURCE="$PROJECT_DIR/../../docs/assets/sixsentences-mark.svg"
ICONSET_DIR="$MODULE_CACHE_DIR/AppIcon.iconset"
COMMUNITY_ORIGIN="${SIX_COMMUNITY_ORIGIN:-}"

[[ -n "$COMMUNITY_ORIGIN" ]] || {
    echo "SIX_COMMUNITY_ORIGIN is required (for example https://research.example.org)" >&2
    exit 1
}
COMMUNITY_ORIGIN="${COMMUNITY_ORIGIN%/}"
if [[ "$COMMUNITY_ORIGIN" == https://* ]]; then
    ORIGIN_AUTHORITY="${COMMUNITY_ORIGIN#https://}"
    if [[ -z "$ORIGIN_AUTHORITY" || "$ORIGIN_AUTHORITY" == *['/?#@ ']* ]]; then
        echo "SIX_COMMUNITY_ORIGIN must be one HTTPS origin without credentials, path, query, or fragment" >&2
        exit 1
    fi
elif [[ "$BUILD_CONFIGURATION" == "debug" && "$COMMUNITY_ORIGIN" == http://* ]]; then
    LOOPBACK_AUTHORITY="${COMMUNITY_ORIGIN#http://}"
    case "$LOOPBACK_AUTHORITY" in
        localhost|127.0.0.1|'[::1]') ;;
        localhost:*|127.0.0.1:*|'[::1]':*)
            LOOPBACK_PORT="${LOOPBACK_AUTHORITY##*:}"
            [[ "$LOOPBACK_PORT" =~ ^[0-9]+$ ]] || {
                echo "The loopback development origin has an invalid port" >&2
                exit 1
            }
            ;;
        *)
            echo "HTTP is accepted only for a loopback debug build" >&2
            exit 1
            ;;
    esac
else
    echo "Release builds require an HTTPS SIX_COMMUNITY_ORIGIN" >&2
    exit 1
fi

mkdir -p "$MODULE_CACHE_DIR" "$EXECUTABLE_DIR" "$RESOURCES_DIR" "$FRAMEWORKS_DIR"
CLANG_MODULE_CACHE_PATH="$MODULE_CACHE_DIR/clang" \
SWIFTPM_MODULECACHE_OVERRIDE="$MODULE_CACHE_DIR/swift" \
swift build --package-path "$PROJECT_DIR" --configuration "$BUILD_CONFIGURATION" --disable-sandbox

cp "$PROJECT_DIR/.build/$BUILD_CONFIGURATION/SixSentencesCompanion" \
    "$EXECUTABLE_DIR/SixSentencesCompanion"
cp "$PROJECT_DIR/AppBundle/Info.plist" "$CONTENTS_DIR/Info.plist"
plutil -insert SixSentencesCommunityOrigin -string "$COMMUNITY_ORIGIN" "$CONTENTS_DIR/Info.plist"
cp "$PROJECT_DIR/AppBundle/ThirdPartyNotices.txt" "$RESOURCES_DIR/ThirdPartyNotices.txt"

SPARKLE_FRAMEWORK="$(find "$PROJECT_DIR/.build" -type d \
    -path '*/Sparkle.xcframework/*/Sparkle.framework' -print -quit)"
if [[ -z "$SPARKLE_FRAMEWORK" ]]; then
    SPARKLE_FRAMEWORK="$(find "$PROJECT_DIR/.build" -type d \
        -name Sparkle.framework -print -quit)"
fi
if [[ -z "$SPARKLE_FRAMEWORK" ]]; then
    echo "Resolved Sparkle.framework is missing; run swift package resolve" >&2
    exit 1
fi
if [[ -e "$FRAMEWORKS_DIR/Sparkle.framework" ]]; then
    find "$FRAMEWORKS_DIR/Sparkle.framework" -depth -delete
fi
ditto "$SPARKLE_FRAMEWORK" "$FRAMEWORKS_DIR/Sparkle.framework"

if ! otool -l "$EXECUTABLE_DIR/SixSentencesCompanion" \
    | grep -Fq '@executable_path/../Frameworks'; then
    install_name_tool -add_rpath '@executable_path/../Frameworks' \
        "$EXECUTABLE_DIR/SixSentencesCompanion"
fi

SPARKLE_APPCAST_URL="${SPARKLE_APPCAST_URL:-}"
SPARKLE_PUBLIC_ED_KEY="${SPARKLE_PUBLIC_ED_KEY:-}"
if [[ -n "$SPARKLE_APPCAST_URL" || -n "$SPARKLE_PUBLIC_ED_KEY" ]]; then
    [[ -n "$SPARKLE_APPCAST_URL" && -n "$SPARKLE_PUBLIC_ED_KEY" ]] || {
        echo "SPARKLE_APPCAST_URL and SPARKLE_PUBLIC_ED_KEY must be provided together" >&2
        exit 1
    }
    plutil -insert SUFeedURL -string "$SPARKLE_APPCAST_URL" "$CONTENTS_DIR/Info.plist"
    plutil -insert SUPublicEDKey -string "$SPARKLE_PUBLIC_ED_KEY" "$CONTENTS_DIR/Info.plist"
fi

if [[ -f "$ICON_SOURCE" ]]; then
    mkdir -p "$ICONSET_DIR"
    BASE_ICON="$MODULE_CACHE_DIR/AppIcon-1024.png"
    if command -v rsvg-convert >/dev/null 2>&1; then
        rsvg-convert -w 1024 -h 1024 "$ICON_SOURCE" -o "$BASE_ICON"
    elif [[ -f "$PROJECT_DIR/AppBundle/AppIcon-1024.png" ]]; then
        cp "$PROJECT_DIR/AppBundle/AppIcon-1024.png" "$BASE_ICON"
    else
        echo "Install librsvg (rsvg-convert) or add AppBundle/AppIcon-1024.png to render the brand icon" >&2
        exit 1
    fi
    for size in 16 32 128 256 512; do
        sips -z "$size" "$size" "$BASE_ICON" \
            --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
        double_size=$((size * 2))
        sips -z "$double_size" "$double_size" "$BASE_ICON" \
            --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
    done
    ICNS_ASSET_DIR="$MODULE_CACHE_DIR/IconAssets.xcassets/AppIcon.appiconset"
    mkdir -p "$ICNS_ASSET_DIR"
    cp "$ICONSET_DIR"/*.png "$ICNS_ASSET_DIR/"
    cat > "$ICNS_ASSET_DIR/Contents.json" <<'JSON'
{"images":[
{"filename":"icon_16x16.png","idiom":"mac","scale":"1x","size":"16x16"},
{"filename":"icon_16x16@2x.png","idiom":"mac","scale":"2x","size":"16x16"},
{"filename":"icon_32x32.png","idiom":"mac","scale":"1x","size":"32x32"},
{"filename":"icon_32x32@2x.png","idiom":"mac","scale":"2x","size":"32x32"},
{"filename":"icon_128x128.png","idiom":"mac","scale":"1x","size":"128x128"},
{"filename":"icon_128x128@2x.png","idiom":"mac","scale":"2x","size":"128x128"},
{"filename":"icon_256x256.png","idiom":"mac","scale":"1x","size":"256x256"},
{"filename":"icon_256x256@2x.png","idiom":"mac","scale":"2x","size":"256x256"},
{"filename":"icon_512x512.png","idiom":"mac","scale":"1x","size":"512x512"},
{"filename":"icon_512x512@2x.png","idiom":"mac","scale":"2x","size":"512x512"}],
"info":{"author":"xcode","version":1}}
JSON
    xcrun actool "$MODULE_CACHE_DIR/IconAssets.xcassets" \
        --compile "$RESOURCES_DIR" \
        --platform macosx \
        --minimum-deployment-target 14.0 \
        --app-icon AppIcon \
        --output-partial-info-plist "$MODULE_CACHE_DIR/icon-partial.plist" >/dev/null
else
    echo "Brand icon is missing at $ICON_SOURCE" >&2
    exit 1
fi

SIGNING_IDENTITY="${SIGNING_IDENTITY:--}"
SIGNING_TIMESTAMP="--timestamp"
if [[ "$SIGNING_IDENTITY" == "-" ]]; then
    SIGNING_TIMESTAMP="--timestamp=none"
fi

# Sign nested code from the inside out. Applying the app's microphone and
# speech-recognition entitlements with `codesign --deep` would incorrectly grant
# them to Sparkle's updater and XPC helpers as well. The helpers intentionally
# receive no app entitlements; only the main application bundle receives them.
SPARKLE_VERSION_DIR="$FRAMEWORKS_DIR/Sparkle.framework/Versions/B"
for nested_code in \
    "$SPARKLE_VERSION_DIR/Updater.app" \
    "$SPARKLE_VERSION_DIR/XPCServices/Downloader.xpc" \
    "$SPARKLE_VERSION_DIR/XPCServices/Installer.xpc" \
    "$SPARKLE_VERSION_DIR/Autoupdate"; do
    [[ -e "$nested_code" ]] || {
        echo "Required Sparkle code is missing: $nested_code" >&2
        exit 1
    }
    codesign --force --options runtime "$SIGNING_TIMESTAMP" \
        --sign "$SIGNING_IDENTITY" "$nested_code"
done
codesign --force --options runtime "$SIGNING_TIMESTAMP" \
    --sign "$SIGNING_IDENTITY" "$FRAMEWORKS_DIR/Sparkle.framework"
codesign --force --options runtime "$SIGNING_TIMESTAMP" \
    --entitlements "$PROJECT_DIR/AppBundle/SixSentencesCompanion.entitlements" \
    --sign "$SIGNING_IDENTITY" "$BUNDLE_DIR"
codesign --verify --deep --strict "$BUNDLE_DIR"

echo "$BUNDLE_DIR"

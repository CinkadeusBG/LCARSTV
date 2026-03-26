#!/bin/bash
# =============================================================================
# revert_media_folders.sh
# Reverts the media folder names back to the original call-sign names.
#
# Renames show-name directories back to their original W/K call signs:
#   ds9               → KDSN
#   family-ties       → KFTS
#   lost-in-space     → KLIS
#   quantum-leap      → KQLP
#   tos               → KTOS
#   voyager           → KVOY
#   andy-griffith     → WAGS
#   a-team            → WATM
#   cheers            → WCHS
#   golden-girls      → WGGS
#   knight-rider      → WKRD
#   star-trek-movies  → WMOV
#   mash              → WMSH
#   twilight-zone     → WTLZ
#   tng               → WTNG
#   weather-channel   → TWC
#
# Files inside each directory are NOT moved — only the directory is renamed.
# commercials/ and artifacts/ are left untouched.
# =============================================================================

MEDIA_DIR="/srv/smb/media"

# Abort if media directory doesn't exist
if [ ! -d "$MEDIA_DIR" ]; then
    echo "ERROR: Media directory not found: $MEDIA_DIR"
    exit 1
fi

# -----------------------------------------------------------------------------
# Function: revert_dir
#   $1 = current directory name  (e.g., ds9)
#   $2 = original call-sign name (e.g., KDSN)
# -----------------------------------------------------------------------------
revert_dir() {
    local current="$1"
    local original="$2"

    local current_path="$MEDIA_DIR/$current"
    local original_path="$MEDIA_DIR/$original"

    echo ""
    echo "----------------------------------------------------------------------"
    echo "Reverting: $current  →  $original"

    # Skip if already using original name
    if [ ! -d "$current_path" ] && [ -d "$original_path" ]; then
        echo "  Already reverted (original name exists): $original"
        return
    fi

    # Check current directory exists
    if [ ! -d "$current_path" ]; then
        echo "  WARNING: Directory not found, skipping: $current_path"
        return
    fi

    # Check that original name is not already taken
    if [ -d "$original_path" ]; then
        echo "  WARNING: Target already exists, skipping: $original_path"
        return
    fi

    mv "$current_path" "$original_path"
    local count
    count=$(find "$original_path" -maxdepth 1 -type f | wc -l)
    echo "  Done: $original/ ($count files)"
}

# =============================================================================
# Main: Rename all directories back to original call signs
# =============================================================================

echo "============================================================"
echo "  TvSimulator Media Folder Revert Script"
echo "  Media directory: $MEDIA_DIR"
echo "============================================================"

#          CURRENT NAME       ORIGINAL CALL SIGN
revert_dir "ds9"               "KDSN"
revert_dir "family-ties"       "KFTS"
revert_dir "lost-in-space"     "KLIS"
revert_dir "quantum-leap"      "KQLP"
revert_dir "tos"               "KTOS"
revert_dir "voyager"           "KVOY"
revert_dir "andy-griffith"     "WAGS"
revert_dir "a-team"            "WATM"
revert_dir "cheers"            "WCHS"
revert_dir "golden-girls"      "WGGS"
revert_dir "knight-rider"      "WKRD"
revert_dir "star-trek-movies"  "WMOV"
revert_dir "mash"              "WMSH"
revert_dir "twilight-zone"     "WTLZ"
revert_dir "tng"               "WTNG"
revert_dir "weather-channel"   "TWC"

# =============================================================================
# Summary
# =============================================================================

echo ""
echo "============================================================"
echo "  Revert complete!"
echo "============================================================"
echo ""
echo "Restored structure in $MEDIA_DIR:"
echo ""
echo "  KDSN/   KFTS/   KLIS/   KQLP/   KTOS/   KVOY/"
echo "  WAGS/   WATM/   WCHS/   WGGS/   WKRD/   WMOV/"
echo "  WMSH/   WTLZ/   WTNG/   TWC/"
echo "  commercials/  (unchanged)"
echo "  artifacts/    (unchanged)"
echo ""

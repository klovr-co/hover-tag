#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
# Store the Apple signing and notarization credentials as GitHub secrets, so
# release builds of Tag.app are signed and notarized. Run it yourself on the
# Mac that has the Developer ID certificate; nothing is printed or saved.
#
#   desktop/scripts/set-apple-secrets.sh path/to/DeveloperID.p12
#
# Before running:
#   1. In Xcode › Settings › Accounts › Manage Certificates, add a
#      "Developer ID Application" certificate (needs the Account Holder role).
#   2. In Keychain Access, find "Developer ID Application: …", right-click ›
#      Export, save as .p12, and choose a password for the file.
#   3. At account.apple.com › Sign-In and Security › App-Specific Passwords,
#      create one named "Tag notarization".
set -eu
repo=klovr-co/hover-tag
p12=${1:-}
[ -f "$p12" ] || { printf 'Usage: %s path/to/DeveloperID.p12\n' "$0" >&2; exit 2; }
command -v gh >/dev/null || { echo "Install the GitHub CLI (gh) and run gh auth login first." >&2; exit 1; }

identity=$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -n 1)
[ -n "$identity" ] || { echo "No Developer ID Application certificate in your keychain (step 1)." >&2; exit 1; }
team=$(printf '%s' "$identity" | sed -n 's/.*(\([A-Z0-9]\{10\}\))$/\1/p')
printf 'Signing identity: %s\nTeam ID: %s\n' "$identity" "$team"

ask() { # ask PROMPT VAR [secret]
    printf '%s' "$1" >&2
    if [ "${3:-}" = secret ]; then stty -echo; fi
    IFS= read -r value
    if [ "${3:-}" = secret ]; then stty echo; printf '\n' >&2; fi
    eval "$2=\$value"
}
ask "Password you chose for the .p12 file: " p12_password secret
ask "Apple ID email (the developer account): " apple_id
ask "App-specific password: " app_password secret

# Check everything with Apple before storing anything.
export TAG_P12_PASSWORD="$p12_password"  # not on the command line, where other processes could see it
{ openssl pkcs12 -in "$p12" -passin env:TAG_P12_PASSWORD -noout 2>/dev/null \
    || openssl pkcs12 -legacy -in "$p12" -passin env:TAG_P12_PASSWORD -noout 2>/dev/null; } \
    || { echo "The .p12 password is wrong." >&2; exit 1; }
echo "Certificate file: password OK"
xcrun notarytool history --apple-id "$apple_id" --team-id "$team" --password "$app_password" >/dev/null \
    || { echo "Apple rejected the Apple ID, app-specific password, or team." >&2; exit 1; }
echo "Notarization credentials: OK"

base64 < "$p12" | tr -d '\n' | gh secret set APPLE_CERTIFICATE -R "$repo"
printf '%s' "$p12_password" | gh secret set APPLE_CERTIFICATE_PASSWORD -R "$repo"
printf '%s' "$identity" | gh secret set APPLE_SIGNING_IDENTITY -R "$repo"
printf '%s' "$apple_id" | gh secret set APPLE_ID -R "$repo"
printf '%s' "$app_password" | gh secret set APPLE_PASSWORD -R "$repo"
printf '%s' "$team" | gh secret set APPLE_TEAM_ID -R "$repo"
echo "Saved. The next release builds a signed, notarized Tag.app. You can delete $p12 now."

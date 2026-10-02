#!/bin/sh
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
swiftc -enable-bare-slash-regex -parse-as-library -D TAG_SETUP_TEST \
    "$here"/*.swift "$here/tests/SetupTests.swift" -o "$build/setup-tests"
"$build/setup-tests"

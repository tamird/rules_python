#!/usr/bin/env bash
set -euxo pipefail

tmpdir="${TEST_TMPDIR}/external_binary"
mkdir -p "${tmpdir}"
tar xf "$1" -C "${tmpdir}"
shift
cd "${tmpdir}"
output="$("$@")"
test "$output" = "token"

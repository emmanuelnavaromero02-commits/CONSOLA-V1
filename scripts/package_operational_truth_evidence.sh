#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 SOURCE_DIR OUTPUT.tar.gz" >&2
  exit 64
fi

source_dir="$1"
output_archive="$2"
output_checksum="${output_archive}.sha256"

if [[ ! -d "$source_dir" ]]; then
  echo "evidence source directory is unavailable" >&2
  exit 1
fi
if [[ "$output_archive" != *.tar.gz ]]; then
  echo "evidence output must end in .tar.gz" >&2
  exit 1
fi
if ! find "$source_dir" -type f -print -quit | grep -q .; then
  echo "evidence source directory contains no files" >&2
  exit 1
fi

source_parent="$(cd "$(dirname "$source_dir")" && pwd -P)"
source_name="$(basename "$source_dir")"
output_parent="$(cd "$(dirname "$output_archive")" && pwd -P)"
output_name="$(basename "$output_archive")"

case "$output_parent/$output_name" in
  "$source_parent/$source_name"/*)
    echo "evidence output cannot be inside the source directory" >&2
    exit 1
    ;;
esac

stage_dir="$(mktemp -d "$output_parent/.operational-truth-evidence.XXXXXX")"
trap 'rm -rf -- "$stage_dir"' EXIT
stage_archive="$stage_dir/$output_name"
stage_checksum="$stage_archive.sha256"
stage_listing="$stage_dir/archive.list"

COPYFILE_DISABLE=1 tar -C "$source_parent" -czf "$stage_archive" "$source_name"
test -s "$stage_archive"
tar -tzf "$stage_archive" > "$stage_listing"
test -s "$stage_listing"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$stage_dir" && sha256sum "$output_name" > "${output_name}.sha256")
  (cd "$stage_dir" && sha256sum -c "${output_name}.sha256")
else
  (cd "$stage_dir" && shasum -a 256 "$output_name" > "${output_name}.sha256")
  (cd "$stage_dir" && shasum -a 256 -c "${output_name}.sha256")
fi
test -s "$stage_checksum"

mv "$stage_archive" "$output_archive"
mv "$stage_checksum" "$output_checksum"

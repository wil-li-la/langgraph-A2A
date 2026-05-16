#!/usr/bin/env bash
# Install a pinned MediaMTX binary into this dir. No system-level install,
# no auto-update, no daemon yet — that's the operator's choice. To run as
# a systemd service after install, see ./mediamtx.service.example.
#
# Usage:
#   ./install.sh                  # latest pinned version
#   MEDIAMTX_VERSION=1.7.0 ./install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${MEDIAMTX_VERSION:-1.9.3}"
ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64) GOARCH=amd64 ;;
    aarch64|arm64) GOARCH=arm64v8 ;;
    *) echo "[ERROR] unsupported arch ${ARCH}" >&2; exit 1 ;;
esac

URL="https://github.com/bluenviron/mediamtx/releases/download/v${VERSION}/mediamtx_v${VERSION}_linux_${GOARCH}.tar.gz"
TARBALL="${SCRIPT_DIR}/mediamtx_v${VERSION}.tar.gz"

if [[ -x "${SCRIPT_DIR}/mediamtx" ]] && "${SCRIPT_DIR}/mediamtx" --version 2>/dev/null | grep -q "${VERSION}"; then
    echo "[install] mediamtx v${VERSION} already present"
    exit 0
fi

echo "[install] fetching ${URL}"
curl -fL --progress-bar -o "${TARBALL}" "${URL}"
tar -xzf "${TARBALL}" -C "${SCRIPT_DIR}" mediamtx
rm "${TARBALL}"
chmod +x "${SCRIPT_DIR}/mediamtx"

echo "[install] installed: $("${SCRIPT_DIR}/mediamtx" --version)"
echo "[install] start with: ./mediamtx ${SCRIPT_DIR}/mediamtx.yml"

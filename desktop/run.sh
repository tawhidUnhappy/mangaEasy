#!/usr/bin/env bash
# Launches the Electron app in dev mode — same as the manual 3-line Git Bash
# sequence used throughout this session, just packaged so it doesn't need
# retyping. Run from anywhere: `bash desktop/run.sh` or `./run.sh` from
# inside desktop/.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$(pwd)/../.tools/node:$PATH"
unset ELECTRON_RUN_AS_NODE
npm run dev

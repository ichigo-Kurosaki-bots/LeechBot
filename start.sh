#!/bin/bash
set -e

aria2c --enable-rpc \
  --rpc-listen-all=false \
  --rpc-listen-port=6800 \
  --max-connection-per-server=4 \
  --split=4 \
  --min-split-size=10M \
  --seed-time=0 \
  --daemon=true

exec python3 bot.py

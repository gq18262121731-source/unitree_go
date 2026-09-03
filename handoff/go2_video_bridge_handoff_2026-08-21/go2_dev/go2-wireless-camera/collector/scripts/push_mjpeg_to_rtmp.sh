#!/usr/bin/env bash
set -euo pipefail

MJPEG_URL="${MJPEG_URL:-http://127.0.0.1:8091/stream.mjpg}"
RTMP_URL="${RTMP_URL:-}"
VIDEO_FPS="${VIDEO_FPS:-15}"
VIDEO_SIZE="${VIDEO_SIZE:-1920x1080}"
VIDEO_BITRATE="${VIDEO_BITRATE:-2500k}"

if [[ -z "$RTMP_URL" ]]; then
  echo "RTMP_URL is required, for example:"
  echo "  RTMP_URL=rtmp://your-server/live/go2x-001 $0"
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is not installed or not in PATH."
  exit 3
fi

exec ffmpeg \
  -hide_banner \
  -loglevel info \
  -reconnect 1 \
  -reconnect_streamed 1 \
  -reconnect_delay_max 2 \
  -f mjpeg \
  -framerate "$VIDEO_FPS" \
  -i "$MJPEG_URL" \
  -an \
  -vf "fps=${VIDEO_FPS},scale=${VIDEO_SIZE}:force_original_aspect_ratio=decrease,pad=${VIDEO_SIZE}:(ow-iw)/2:(oh-ih)/2" \
  -c:v libx264 \
  -preset veryfast \
  -tune zerolatency \
  -b:v "$VIDEO_BITRATE" \
  -maxrate "$VIDEO_BITRATE" \
  -bufsize "$(( ${VIDEO_BITRATE%k} * 2 ))k" \
  -pix_fmt yuv420p \
  -f flv \
  "$RTMP_URL"

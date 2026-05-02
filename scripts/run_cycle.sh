#!/bin/zsh
# launchd fires this every 10 min. We act as a random-interval gate:
#  - Only run if past data/next_run (computed last run = now + random in window).
#  - Only run between schedule.start_hour and schedule.end_hour from config.yaml.
#  - All output appended to logs/run.log.

PROJECT_DIR="/Users/skkhan/workarea/Facebook-Automation"
LOG="$PROJECT_DIR/logs/run.log"
NEXT_RUN_FILE="$PROJECT_DIR/data/next_run"
PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"

# Read schedule from config.yaml (with defaults if missing/malformed)
SCHED=$("$PYTHON" - <<'PY' 2>/dev/null
import yaml
try:
    s = (yaml.safe_load(open("config.yaml")) or {}).get("schedule", {}) or {}
except Exception:
    s = {}
print(
    s.get("start_hour", 8),
    s.get("end_hour", 21),
    s.get("min_interval_seconds", 1200),
    s.get("max_interval_seconds", 3000),
)
PY
)
read -r START_HOUR END_HOUR MIN_INT MAX_INT <<< "$SCHED"

# Sanity defaults if Python failed (e.g. venv missing)
[ -z "$START_HOUR" ] && START_HOUR=8
[ -z "$END_HOUR" ] && END_HOUR=21
[ -z "$MIN_INT" ] && MIN_INT=1200
[ -z "$MAX_INT" ] && MAX_INT=3000

# Time-of-day gate
hour=$(date +%H)
hour=$((10#$hour))   # strip leading zero (e.g., "08" -> 8)
if [ "$hour" -lt "$START_HOUR" ] || [ "$hour" -ge "$END_HOUR" ]; then
    exit 0
fi

# Random-interval gate: only run if now >= next_run
now_epoch=$(date +%s)
if [ -f "$NEXT_RUN_FILE" ]; then
    next_epoch=$(cat "$NEXT_RUN_FILE" 2>/dev/null || echo 0)
    if [ "$now_epoch" -lt "$next_epoch" ]; then
        exit 0
    fi
fi

# Schedule the next run before we start
range=$((MAX_INT - MIN_INT + 1))
[ "$range" -lt 1 ] && range=1
delay=$(( MIN_INT + RANDOM % range ))
echo $((now_epoch + delay)) > "$NEXT_RUN_FILE"

{
    echo ""
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') start cycle (window=${START_HOUR}-${END_HOUR}, next run in ${delay}s) ==="
    source "$PROJECT_DIR/.venv/bin/activate"
    python -m src.main run --once
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') end cycle ==="
} >> "$LOG" 2>&1

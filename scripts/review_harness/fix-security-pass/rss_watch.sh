#!/bin/sh
# Run a command, sample its RSS every 0.5 s, kill it past LIMIT_MIB (default 6144); print the peak.
#   LIMIT_MIB=6144 sh scripts/review_harness/fix-security-pass/rss_watch.sh python ...
LIMIT=${LIMIT_MIB:-6144}
"$@" &
pid=$!
peak=0; start=$(date +%s)
while kill -0 $pid 2>/dev/null; do
  rss=$(awk '/VmRSS/{print int($2/1024)}' /proc/$pid/status 2>/dev/null || echo 0)
  [ -n "$rss" ] && [ "$rss" -gt "$peak" ] && peak=$rss
  if [ "$peak" -gt "$LIMIT" ]; then kill -9 $pid; echo "killed by watcher at ${peak}MiB after $(( $(date +%s) - start ))s"; break; fi
  sleep 0.5
done
wait $pid 2>/dev/null
echo "sampled peak ${peak}MiB, $(( $(date +%s) - start ))s"

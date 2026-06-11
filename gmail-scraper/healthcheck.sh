#!/bin/sh
# Check if heartbeat file was updated in last 120 seconds
if [ ! -f /tmp/healthcheck ]; then exit 1; fi
last=$(cat /tmp/healthcheck)
now=$(date +%s)
diff=$((now - ${last%.*}))
[ $diff -lt 120 ] && exit 0 || exit 1

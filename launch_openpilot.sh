#!/usr/bin/bash

if [ -f /EON ]; then
    TIMEZONE=$(cat /data/params/d/Timezone 2>/dev/null)
    # Use the same supported choices as the UI and timezoned.
    if ! grep -Fxq -- "$TIMEZONE" "$(dirname "$0")/selfdrive/assets/timezones.txt"; then
        TIMEZONE="Asia/Seoul"
    fi
    if [ "$(getprop persist.sys.timezone)" != "$TIMEZONE" ]; then
        setprop persist.sys.timezone "$TIMEZONE"
    fi
fi

export PASSIVE="0"
exec ./launch_chffrplus.sh


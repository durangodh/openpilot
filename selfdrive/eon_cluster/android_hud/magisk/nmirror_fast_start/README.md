# nMirror Fast Start 1.7

This updates the existing rooted-S9 module (`id=nmirror_fast_start`). Keep the
existing `config.sh` and wireless receiver selection. Replace `service.sh` and
`module.prop` together; changes take effect on the next normal boot.

Navigation activities belong on **display 0** with nMirror 0.1.18. Its
`nMirror2 capture` virtual display is a capture surface, not an app destination.
Check the resumed activity, not arbitrary task-history text. Observe the full
180-second startup window instead of treating an early foreground app as proof
that vehicle projection succeeded. RemoteHUD also retries on capture-display
events for connections after this window.

The bounded persistent log now includes RemoteHudBoot, AppsOnStart and Power.
RemoteHUD keeps its own two bounded app-private `files/hud-session*.log` files
so buffer density, USB preparation and navigation decisions survive reboot.

No navigation preferences, Bluetooth pairings or nMirror settings are changed.

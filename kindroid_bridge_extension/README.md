# TinyTable Kindroid receive bridge

This is an explicit rollback/fallback tool for the Kindroid sidecar, not the
default transport. The TinyTable API must have
`TINYTABLE_KINDROID_BRIDGE_ENABLED=1`, and provider events still have to pass
the configured three-factor identity binding.

Before loading it, edit `config.js`: for each Kindroid chat, set `chatPath` to
the part of the chat's address after `kindroid.ai` and map the name Kindroid
shows in `Message from <name>` to its TinyTable seat. Chats without a
`chatPath` are ignored, so nothing is forwarded until you fill it in.

The bridge discovers open TinyTable localhost tabs and tries their origin
first; it also supports the usual SSH-tunnel port `18000` and local port
`8000`. Reload the unpacked extension after changing this folder.

This unpacked Chromium extension observes only rendered Kindroid DOM messages
whose heading is `Message from <a configured name>`. It forwards the visible
text, the message DOM ID, and the rendered timestamp to TinyTable as a
browser-observed Kindroid message for the configured seat.

It does not open sockets, inspect network requests, or read credentials. It
supports a separate explicit send request from TinyTable by filling the
visible `textarea[aria-label="Message"]` and clicking Kindroid’s native
`button[aria-label="Send message"]` control.
It never sends automatically and is not enabled automatically when the
server-side Kindroid adapter is unavailable.

## Loading

1. Keep TinyTable running locally.
2. Open `chrome://extensions`.
3. Enable Developer mode.
4. Choose **Load unpacked**.
5. Select this `kindroid_bridge_extension` folder.
6. Reload the Kindroid tab.

The Kindroid console should show `TinyTable received a Kindroid
kindroid-companion message` when a new companion message appears. Existing
visible messages may also be
forwarded once; TinyTable deduplicates them by their DOM ID.

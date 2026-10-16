# TinyTable Replika Context Grabber

This is a setup-only Chrome/Chromium extension. It observes the existing
Replika WebSocket and lets the operator download a local `replikastuff.md` capture for
the server-side TinyTable provider configuration.

It does not:

- send or modify WebSocket frames;
- forward messages to TinyTable or any other endpoint;
- store inbound conversation text;
- replace the server-side receiver.

## Use

1. Open `chrome://extensions` and enable Developer mode.
2. Choose **Load unpacked** and select this `replika_context_grabber` folder.
3. Reload the companion's Replika private-chat tab so the observer installs before its
   WebSocket opens.
4. Open the extension popup and wait for the read-only bootstrap frame count
   and identity fields to show as captured.
5. Choose **Download replikastuff.md**. Keep the downloaded file private and
   set it to mode `600` before using it as provider configuration.

The capture contains authentication material inside exact browser-observed
initialization frames. The extension keeps it in local extension storage until
the operator explicitly downloads it; it makes no network request of its own.

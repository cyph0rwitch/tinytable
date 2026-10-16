# TinyTable Replika receive bridge

This unpacked Chrome/Chromium extension observes the Replika page's existing
WebSocket connection. It forwards only validated inbound `Robot` messages to
TinyTable's local provider-ingest endpoint.

It does not create a WebSocket, does not call `WebSocket.send()`, and does not
forward auth tokens, customer IDs, or raw provider frames.

## Loading it

1. Keep the TinyTable server running locally.
2. Open `chrome://extensions` in Chrome or Chromium.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select this `replika_bridge_extension` folder.
6. Reload the Replika tab so the observer installs before Replika opens its
   WebSocket.

The browser console may show `TinyTable received a Replika Robot message` for
each forwarded message. The duplicate-safe TinyTable endpoint preserves the
provider message ID and provenance.

TinyTable's direct **Send to** (Replika companion) action can also use this already-open
browser session. TinyTable asks for confirmation for the exact text first; the
bridge sends only after that confirmation and only when it has observed the
current session context. Approved outbound messages include their required
disclosure before the browser handoff.

This bridge is receive-only with respect to Replika, but the normal Replika
page may still send its own ordinary session frames. Direct sending is a
separate explicit action initiated from TinyTable; the bridge does not invent
messages or perform automatic routing.

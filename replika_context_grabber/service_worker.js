"use strict";

const STORAGE_KEY = "tinytableReplikaContext";
const MAX_BOOTSTRAP_FRAMES = 32;

const emptyState = () => ({
  captured_at: new Date().toISOString(),
  ws_url: "",
  identity: {chat_id: "", bot_id: "", customer_id: ""},
  bootstrap: []
});

const frameKey = (frame) => JSON.stringify([frame.event_name, frame.frame]);

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request?.type !== "tinytable-replika-context" || !request.capture) return false;
  chrome.storage.local.get(STORAGE_KEY).then((stored) => {
    const state = stored[STORAGE_KEY] || emptyState();
    const capture = request.capture;
    state.captured_at = new Date().toISOString();
    if (capture.ws_url) state.ws_url = capture.ws_url;
    if (capture.kind === "identity") {
      for (const field of ["chat_id", "bot_id", "customer_id"]) {
        if (typeof capture[field] === "string" && capture[field]) state.identity[field] = capture[field];
      }
    }
    if (capture.kind === "bootstrap" && capture.frame) {
      const key = frameKey(capture);
      if (!state.bootstrap.some((item) => frameKey(item) === key)) {
        state.bootstrap.push(capture);
        state.bootstrap = state.bootstrap.slice(-MAX_BOOTSTRAP_FRAMES);
      }
    }
    return chrome.storage.local.set({[STORAGE_KEY]: state});
  }).then(() => sendResponse({ok: true})).catch((error) => sendResponse({ok: false, error: error.message}));
  return true;
});

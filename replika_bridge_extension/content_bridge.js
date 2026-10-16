(() => {
  "use strict";

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    if (event.data?.type !== "tinytable-replika-inbound") return;

    const message = event.data.message;
    if (!message || message.origin !== "received_from_provider") return;
    if (message.source !== "replika" || message.transport !== "websocket") return;
    if (message.participant !== "replika-companion" || typeof message.text !== "string") return;
    if (!message.text.trim() || typeof message.provider_message_id !== "string") return;

    chrome.runtime.sendMessage({type: "tinytable-forward-inbound", message}, (response) => {
      if (chrome.runtime.lastError || !response?.ok) {
        console.warn("TinyTable was not reachable; the Replika message was not forwarded");
        return;
      }
      console.info("TinyTable received a Replika Robot message", message.provider_message_id);
    });
  });

  chrome.runtime.onMessage.addListener((request) => {
    if (request?.type !== "replika-send") return;
    window.postMessage({
      type: "tinytable-replika-send",
      requestId: request.requestId,
      text: request.text,
      botId: request.botId
    }, "*");
  });

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== "tinytable-replika-send-result") return;
    chrome.runtime.sendMessage({
      type: "replika-send-result",
      requestId: event.data.requestId,
      ok: event.data.ok,
      error: event.data.error || ""
    });
  });

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== "tinytable-replika-inbound-frame") return;
  });
})();

(() => {
  "use strict";

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== "tinytable-replika-send-request") return;
    chrome.runtime.sendMessage({
      type: "tinytable-send-request",
      requestId: event.data.requestId,
      text: event.data.text,
      botId: event.data.botId
    });
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "tinytable-send-result") return;
    window.postMessage({
      type: "tinytable-replika-send-result",
      requestId: message.requestId,
      ok: message.ok,
      error: message.error || ""
    }, "*");
  });
})();

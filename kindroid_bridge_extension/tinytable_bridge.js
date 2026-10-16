(() => {
  "use strict";

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== "tinytable-kindroid-send-request") return;
    chrome.runtime.sendMessage({
      type: "tinytable-kindroid-send-request",
      requestId: event.data.requestId,
      text: event.data.text,
      channelId: event.data.channelId
    });
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "tinytable-kindroid-send-result") return;
    window.postMessage({
      type: "tinytable-kindroid-send-result",
      requestId: message.requestId,
      ok: message.ok,
      error: message.error || ""
    }, "*");
  });
})();

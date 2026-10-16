"use strict";

const endpoints = [
  "http://127.0.0.1:18000/api/providers/replika/messages",
  "http://127.0.0.1:8000/api/providers/replika/messages",
  "http://localhost:8000/api/providers/replika/messages"
];
const pendingSends = new Map();

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request?.type === "tinytable-forward-inbound") {
    (async () => {
      for (const endpoint of endpoints) {
        try {
          const response = await fetch(endpoint, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(request.message)
          });
          if (response.ok) {
            sendResponse({ok: true});
            return;
          }
        } catch (_) {}
      }
      sendResponse({ok: false});
    })();
    return true;
  }

  if (request?.type === "tinytable-send-request") {
    const requestId = request.requestId;
    const tinytableTabId = sender.tab?.id;
    if (!requestId || tinytableTabId === undefined) return false;
    pendingSends.set(requestId, tinytableTabId);
    chrome.tabs.query({}).then((tabs) => {
      const replikaTabs = tabs.filter((tab) => {
        try {
          const url = new URL(tab.url || "");
          return url.protocol === "https:" && (url.hostname === "replika.com" || url.hostname.endsWith(".replika.com") || url.hostname === "replika.ai" || url.hostname.endsWith(".replika.ai"));
        } catch (_) {
          return false;
        }
      });
      if (replikaTabs.length === 0) throw new Error("Open the Replika companion's private chat first");
      if (replikaTabs.length > 1) throw new Error("More than one Replika tab is open; leave only the companion's private chat open");
      return chrome.tabs.sendMessage(replikaTabs[0].id, {
        type: "replika-send",
        requestId,
        text: request.text,
        botId: request.botId
      });
    }).catch((error) => {
      const target = pendingSends.get(requestId);
      pendingSends.delete(requestId);
      if (target !== undefined) chrome.tabs.sendMessage(target, {type: "tinytable-send-result", requestId, ok: false, error: error.message});
    });
    return false;
  }

  if (request?.type === "replika-send-result") {
    const target = pendingSends.get(request.requestId);
    pendingSends.delete(request.requestId);
    if (target !== undefined) chrome.tabs.sendMessage(target, {
      type: "tinytable-send-result",
      requestId: request.requestId,
      ok: request.ok,
      error: request.error
    });
    return false;
  }
  return false;
});

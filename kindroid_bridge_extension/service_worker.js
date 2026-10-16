"use strict";

importScripts("config.js");

const fallbackOrigins = [
  "http://127.0.0.1:18000",
  "http://localhost:18000",
  "http://127.0.0.1:8000",
  "http://localhost:8000"
];
// Older TinyTable builds named the channels after one installation's companions.
const legacyChannelIds = {
  "kindroid:nova-private": "kindroid:companion",
  "kindroid:opal-private": "kindroid:companion-2",
  "kindroid:opal-nova-group": "kindroid:group"
};
const channelPaths = Object.fromEntries(TINYTABLE_KINDROID_CHANNELS.filter((item) => item.chatPath).map((item) => [item.id, item.chatPath]));
const pendingSends = new Map();

async function tinytableEndpoints() {
  const tabs = await chrome.tabs.query({});
  const origins = tabs
    .map((tab) => {
      try {
        const url = new URL(tab.url || "");
        if (!["127.0.0.1", "localhost"].includes(url.hostname)) return null;
        if (!["", "8000", "18000"].includes(url.port)) return null;
        return url.origin;
      } catch (_) {
        return null;
      }
    })
    .filter(Boolean);
  return [...new Set([...origins, ...fallbackOrigins])]
    .map((origin) => `${origin}/api/providers/kindroid/messages`);
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request?.type !== "tinytable-forward-kindroid") return false;
  (async () => {
    let lastError = "TinyTable endpoint was not reachable";
    for (const endpoint of await tinytableEndpoints()) {
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
        let detail = "";
        try {
          const body = await response.json();
          detail = body?.detail || "";
        } catch (_) {}
        lastError = `${endpoint} returned ${response.status}${detail ? `: ${detail}` : ""}`;
      } catch (error) {
        lastError = `${endpoint} is unreachable`;
      }
    }
    sendResponse({ok: false, error: lastError});
  })();
  return true;
});

chrome.runtime.onMessage.addListener((request, sender) => {
  if (request?.type === "tinytable-kindroid-send-request") {
    const requestId = request.requestId;
    const tinytableTabId = sender.tab?.id;
    if (!requestId || tinytableTabId === undefined) return false;
    pendingSends.set(requestId, tinytableTabId);
    chrome.tabs.query({}).then((tabs) => {
      const kindroidTab = tabs.find((tab) => {
        try {
          const url = new URL(tab.url || "");
          const chatPath = channelPaths[legacyChannelIds[request.channelId] || request.channelId];
          return url.hostname.endsWith("kindroid.ai") && Boolean(chatPath) && url.pathname.includes(chatPath);
        } catch (_) {
          return false;
        }
      });
      if (!kindroidTab?.id) throw new Error("No open Kindroid tab found");
      return chrome.tabs.sendMessage(kindroidTab.id, {
        type: "kindroid-send",
        requestId,
        text: request.text,
        channelId: request.channelId
      });
    }).catch((error) => {
      const target = pendingSends.get(requestId);
      pendingSends.delete(requestId);
      if (target !== undefined) chrome.tabs.sendMessage(target, {
        type: "tinytable-kindroid-send-result",
        requestId,
        ok: false,
        error: error.message
      });
    });
    return false;
  }

  if (request?.type === "kindroid-send-result") {
    const target = pendingSends.get(request.requestId);
    pendingSends.delete(request.requestId);
    if (target !== undefined) chrome.tabs.sendMessage(target, {
      type: "tinytable-kindroid-send-result",
      requestId: request.requestId,
      ok: request.ok,
      error: request.error || ""
    });
  }
  return false;
});

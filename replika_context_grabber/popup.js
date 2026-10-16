"use strict";

const STORAGE_KEY = "tinytableReplikaContext";
const status = document.getElementById("status");

const getState = async () => {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  return stored[STORAGE_KEY] || {captured_at: "", ws_url: "", identity: {}, bootstrap: []};
};

const render = async () => {
  const state = await getState();
  const identity = state.identity || {};
  status.textContent = [
    `Bootstrap frames: ${(state.bootstrap || []).length}`,
    `chat_id: ${identity.chat_id ? "captured" : "missing"}`,
    `bot_id: ${identity.bot_id ? "captured" : "missing"}`,
    `customer_id: ${identity.customer_id ? "captured" : "missing"}`,
    state.ws_url ? `WebSocket: ${state.ws_url}` : "WebSocket: waiting",
    "Reload the companion's Replika chat tab after installing this extension."
  ].join("\n");
  return state;
};

const markdown = (state) => {
  const identity = state.identity || {};
  const lines = [
    "# TinyTable Replika context capture",
    `captured_at: ${state.captured_at || new Date().toISOString()}`,
    `ws_url: ${state.ws_url || ""}`,
    `chat_id: ${identity.chat_id || ""}`,
    `bot_id: ${identity.bot_id || ""}`,
    `customer_id: ${identity.customer_id || ""}`,
    "",
    "# Exact browser-observed read-only initialization frames",
    ""
  ];
  for (const item of state.bootstrap || []) {
    lines.push("to server:", JSON.stringify(item.frame), "");
  }
  return `${lines.join("\n")}\n`;
};

document.getElementById("download").addEventListener("click", async () => {
  const state = await getState();
  const content = markdown(state);
  const url = URL.createObjectURL(new Blob([content], {type: "text/markdown"}));
  chrome.downloads.download({url, filename: "replikastuff.md", saveAs: true}, () => {
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    if (chrome.runtime.lastError) status.textContent = chrome.runtime.lastError.message;
  });
});

document.getElementById("clear").addEventListener("click", async () => {
  await chrome.storage.local.remove(STORAGE_KEY);
  await render();
});

render();

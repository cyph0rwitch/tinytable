(() => {
  "use strict";

  if (window.__tinytableKindroidBridgeInstalled) return;
  window.__tinytableKindroidBridgeInstalled = true;
  // Chats and speaker names come from config.js; nothing about a companion is built in.
  const configured = TINYTABLE_KINDROID_CHANNELS.find((item) => item.chatPath && location.pathname.includes(item.chatPath)) || null;
  const channel = configured && {
    id: configured.id,
    type: configured.type,
    speakers: Object.fromEntries(Object.entries(configured.speakers).filter(([name]) => name).map(([name, seat]) => [name.toUpperCase(), seat]))
  };
  console.info("TinyTable Kindroid bridge v0.2.0 installed", location.href, channel ? channel.id : "unconfigured chat");

  const seen = new Set();

  const providerTimestamp = (node) => {
    const raw = node.getAttribute("data-chat-timestamp");
    if (!raw) return new Date().toISOString();
    const milliseconds = Number(raw);
    return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : raw;
  };

  const textKey = (text) => {
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `text:${(hash >>> 0).toString(16)}:${text.length}`;
  };

  const normalize = (node) => {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return null;
    if (node.getAttribute("data-chat-message") !== "true") return null;
    const heading = node.querySelector("h3");
    const speaker = heading?.textContent.trim().match(/^message from\s+(.+)$/i)?.[1]?.trim().toUpperCase();
    if (!channel || !heading || !channel.speakers[speaker]) return null;
    const chatTimestamp = node.getAttribute("data-chat-timestamp")?.trim() || "";
    const surface = node.querySelector('[data-chat-message-surface="true"]');
    const text = surface?.querySelector("p")?.innerText?.trim() || "";
    const providerMessageId = node.id || (chatTimestamp ? `timestamp:${chatTimestamp}` : (text ? textKey(text) : ""));
    if (!providerMessageId || !text) return null;
    return {
      participant: channel.speakers[speaker],
      channel_id: channel.id,
      channel_type: channel.type,
      source: "kindroid",
      transport: "dom_observer",
      origin: "received_from_provider",
      text,
      provider_message_id: providerMessageId,
      timestamp: providerTimestamp(node),
      provider_metadata: {
        dom_id: node.id || null,
        chat_timestamp: chatTimestamp,
        heading: heading.textContent.trim()
      }
    };
  };

  const forward = (node) => {
    const message = normalize(node);
    if (!message || seen.has(message.provider_message_id)) return;
    console.info(`TinyTable found a Kindroid ${message.participant} message`, message.provider_message_id, channel.id);
    seen.add(message.provider_message_id);
    chrome.runtime.sendMessage({type: "tinytable-forward-kindroid", message}, (response) => {
      if (chrome.runtime.lastError || !response?.ok) {
        console.warn("TinyTable did not accept the Kindroid message; the message was not forwarded", response?.error || chrome.runtime.lastError?.message || "unknown error");
        seen.delete(message.provider_message_id);
        return;
      }
      console.info(`TinyTable received a Kindroid ${message.participant} message`, message.provider_message_id, channel.id);
    });
  };

  const scan = (root) => {
    if (root instanceof HTMLElement && root.matches('[data-chat-message="true"]')) forward(root);
    if (root.querySelectorAll) {
      const messages = root.querySelectorAll('[data-chat-message="true"]');
      if (root === document) console.info("TinyTable Kindroid bridge scanned message containers", messages.length);
      messages.forEach(forward);
    }
  };

  scan(document);
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => scan(node));
    }
  });
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true
  });

  // Kindroid can hydrate the chat after document_idle without producing a
  // useful added-node event for the message list. Poll briefly for the first
  // rendered batch, then rely on the mutation observer.
  const hydrationPoll = setInterval(() => {
    const count = document.querySelectorAll('[data-chat-message="true"]').length;
    if (count > 0) {
      scan(document);
      clearInterval(hydrationPoll);
    }
  }, 1000);
  setTimeout(() => clearInterval(hydrationPoll), 30000);

  const sendResult = (requestId, ok, error = "") => {
    chrome.runtime.sendMessage({type: "kindroid-send-result", requestId, ok, error});
  };

  chrome.runtime.onMessage.addListener((request) => {
    if (request?.type !== "kindroid-send") return;
    const requestId = request.requestId;
    const text = typeof request.text === "string" ? request.text.trim() : "";
    if (!requestId || !text) {
      sendResult(requestId, false, "Message text is empty");
      return;
    }
    const textarea = document.querySelector('textarea[aria-label="Message"]');
    if (!textarea) {
      sendResult(requestId, false, "Kindroid message composer was not found");
      return;
    }
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    if (!setter) {
      sendResult(requestId, false, "Kindroid composer value setter was not available");
      return;
    }
    setter.call(textarea, text);
    textarea.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: text}));
    setTimeout(() => {
      const sendButton = document.querySelector('[data-chat-composer-controls] button[aria-label="Send message"]');
      if (!sendButton) {
        sendResult(requestId, false, "Kindroid Send message button was not available after filling the composer");
        return;
      }
      sendButton.click();
      console.info("TinyTable requested an explicit Kindroid send", requestId);
      sendResult(requestId, true);
    }, 50);
  });
})();

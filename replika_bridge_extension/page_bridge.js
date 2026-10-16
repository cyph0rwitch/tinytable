(() => {
  "use strict";

  // This observer never calls send() and never changes the page's WebSocket
  // behavior. It only watches inbound message events from the connection the
  // Replika page already created.
  const NativeWebSocket = window.WebSocket;
  if (!NativeWebSocket || window.__tinytableReplikaBridgeInstalled) return;
  window.__tinytableReplikaBridgeInstalled = true;
  let activeSocket = null;
  let observedAuth = null;
  let observedChatId = null;
  let observedBotId = null;

  const uuid = () => crypto.randomUUID();
  const observeOutbound = (raw) => {
    if (typeof raw !== "string") return;
    try {
      const frame = JSON.parse(raw);
      if (!frame || typeof frame !== "object") return;
      if (frame.auth && typeof frame.auth === "object") {
        const auth = frame.auth;
        if (auth.user_id && auth.auth_token && auth.device_id) {
          observedAuth = {user_id: auth.user_id, auth_token: auth.auth_token, device_id: auth.device_id};
        }
      }
      const payload = frame.payload;
      if (payload?.chat_id) observedChatId = payload.chat_id;
      if (payload?.meta?.chat_id) observedChatId = payload.meta.chat_id;
      if (payload?.meta?.bot_id) observedBotId = payload.meta.bot_id;
    } catch (_) {
      // Ignore non-JSON application frames.
    }
  };

  const nativePrototypeSend = NativeWebSocket.prototype.send;
  Object.defineProperty(NativeWebSocket.prototype, "send", {
    configurable: true,
    writable: true,
    value(raw) {
      observeOutbound(raw);
      return nativePrototypeSend.call(this, raw);
    }
  });

  const publish = (event) => {
    if (!event || event.event_name !== "message") return;
    const payload = event.payload;
    const meta = payload?.meta;
    const content = payload?.content;
    if (meta?.chat_id) observedChatId = meta.chat_id;
    if (meta?.bot_id) observedBotId = meta.bot_id;
    if (meta?.nature !== "Robot") return;
    if (typeof payload?.id !== "string" || !payload.id) return;
    if (typeof meta?.timestamp !== "string" || !meta.timestamp) return;
    if (typeof meta?.bot_id !== "string" || !meta.bot_id) return;
    if (typeof meta?.chat_id !== "string" || !meta.chat_id) return;
    if (typeof content?.text !== "string" || !content.text.trim()) return;

    // Deliberately send a normalized, credential-free object across the page
    // boundary. No auth, token, customer ID, or raw provider frame leaves the
    // page context.
    window.postMessage({
      type: "tinytable-replika-inbound",
      message: {
        participant: "replika-companion",
        source: "replika",
        transport: "websocket",
        origin: "received_from_provider",
        text: content.text,
        provider_message_id: payload.id,
        timestamp: meta.timestamp,
        provider_metadata: {
          nature: meta.nature,
          bot_id: meta.bot_id,
          chat_id: meta.chat_id
        }
      }
    }, "*");
  };

  const ObservedWebSocket = new Proxy(NativeWebSocket, {
    construct(target, args, newTarget) {
      const socket = Reflect.construct(target, args, newTarget);
      activeSocket = socket;
      socket.addEventListener("message", (incoming) => {
        if (typeof incoming.data !== "string") return;
        try {
          const parsed = JSON.parse(incoming.data);
          if (parsed && typeof parsed.event_name === "string") {
            // Ack coordination uses only the event name; never expose the raw
            // provider frame to the extension or TinyTable page.
            window.postMessage({
              type: "tinytable-replika-inbound-frame",
              frame: {event_name: parsed.event_name}
            }, "*");
          }
          publish(parsed);
        } catch (_) {
          // Ignore non-JSON provider frames.
        }
      });
      return socket;
    }
  });

  Object.defineProperty(window, "WebSocket", {
    value: ObservedWebSocket,
    configurable: true,
    writable: true
  });

  const sendResult = (requestId, ok, error = "") => {
    window.postMessage({type: "tinytable-replika-send-result", requestId, ok, error}, "*");
  };

  const waitForEvent = (eventName, timeout = 5000) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      window.removeEventListener("message", onMessage);
      reject(new Error(`Timed out waiting for Replika ${eventName} acknowledgement`));
    }, timeout);
    const onMessage = (event) => {
      if (event.source !== window || event.data?.type !== "tinytable-replika-inbound-frame") return;
      if (event.data.frame?.event_name !== eventName) return;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      resolve();
    };
    window.addEventListener("message", onMessage);
  });

  window.addEventListener("message", async (event) => {
    if (event.source !== window || event.data?.type !== "tinytable-replika-send") return;
    const {requestId, text, botId} = event.data;
    if (typeof requestId !== "string" || typeof text !== "string" || !text.trim()) {
      sendResult(requestId, false, "Message text is empty");
      return;
    }
    if (!activeSocket || activeSocket.readyState !== NativeWebSocket.OPEN) {
      sendResult(requestId, false, "No open Replika WebSocket was found");
      return;
    }
    const sendBotId = observedBotId || (typeof botId === "string" && botId.trim() ? botId.trim() : "");
    if (!observedAuth || !observedChatId || !sendBotId) {
      sendResult(
        requestId,
        false,
        `The Replika session context is not ready yet (auth=${Boolean(observedAuth)}, chat=${Boolean(observedChatId)}, bot=${Boolean(sendBotId)})`
      );
      return;
    }
    try {
      const auth = observedAuth;
      activeSocket.send(JSON.stringify({
        event_name: "text_input_detected",
        payload: {chat_id: observedChatId},
        token: uuid(),
        auth
      }));
      await waitForEvent("text_input_detected");
      activeSocket.send(JSON.stringify({
        event_name: "message",
        payload: {
          content: {type: "text", text},
          meta: {
            bot_id: sendBotId,
            client_token: uuid().toUpperCase(),
            chat_id: observedChatId,
            timestamp: new Date().toISOString()
          }
        },
        token: uuid(),
        auth
      }));
      sendResult(requestId, true);
    } catch (error) {
      sendResult(requestId, false, error instanceof Error ? error.message : "Replika send failed");
    }
  });
})();

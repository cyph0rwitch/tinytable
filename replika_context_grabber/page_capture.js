(() => {
  "use strict";

  if (window.__tinytableContextGrabberInstalled) return;
  const NativeWebSocket = window.WebSocket;
  if (!NativeWebSocket) return;
  window.__tinytableContextGrabberInstalled = true;

  const READ_ONLY_EVENTS = new Set([
    "init",
    "main_screen",
    "chat_screen",
    "application_started",
    "app_foreground",
    "history",
    "chat_suggestions_list"
  ]);

  const publish = (capture) => {
    window.postMessage({type: "tinytable-replika-context", capture}, "*");
  };

  const inspect = (raw, direction, wsUrl) => {
    if (typeof raw !== "string") return;
    let frame;
    try {
      frame = JSON.parse(raw);
    } catch (_) {
      return;
    }
    if (!frame || typeof frame !== "object") return;

    if (direction === "to_server" && READ_ONLY_EVENTS.has(frame.event_name) && frame.auth && typeof frame.auth === "object") {
      // This is the exact browser-observed initialization frame. Customer
      // message frames are deliberately excluded by the allowlist.
      publish({kind: "bootstrap", direction, event_name: frame.event_name, frame, ws_url: wsUrl});
    }

    const payload = frame.payload;
    const meta = payload && typeof payload === "object" && payload.meta && typeof payload.meta === "object"
      ? payload.meta
      : null;
    const chatId = meta?.chat_id || (payload && typeof payload.chat_id === "string" ? payload.chat_id : "");
    const botId = meta?.bot_id || "";
    const customerId = meta?.customer || meta?.customer_id || frame.auth?.user_id || "";
    if (chatId || botId || customerId) {
      // Identity only; no provider text, raw message frame, or token is
      // published for inbound frames.
      publish({
        kind: "identity",
        event_name: frame.event_name || "unknown",
        chat_id: typeof chatId === "string" ? chatId : "",
        bot_id: typeof botId === "string" ? botId : "",
        customer_id: typeof customerId === "string" ? customerId : "",
        ws_url: wsUrl
      });
    }
  };

  const nativeSend = NativeWebSocket.prototype.send;
  Object.defineProperty(NativeWebSocket.prototype, "send", {
    configurable: true,
    writable: true,
    value(raw) {
      inspect(raw, "to_server", this.url || "");
      return nativeSend.call(this, raw);
    }
  });

  const ObservedWebSocket = new Proxy(NativeWebSocket, {
    construct(target, args, newTarget) {
      const socket = Reflect.construct(target, args, newTarget);
      const wsUrl = typeof args[0] === "string" ? args[0] : "";
      socket.addEventListener("message", (event) => inspect(event.data, "to_client", wsUrl));
      return socket;
    }
  });

  Object.defineProperty(window, "WebSocket", {
    value: ObservedWebSocket,
    configurable: true,
    writable: true
  });
})();

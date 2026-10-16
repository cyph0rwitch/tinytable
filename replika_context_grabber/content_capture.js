(() => {
  "use strict";

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== "tinytable-replika-context") return;
    chrome.runtime.sendMessage({type: "tinytable-replika-context", capture: event.data.capture});
  });
})();

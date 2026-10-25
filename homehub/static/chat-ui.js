(() => {
  function setBusy(prompt, sendButton, busy) {
    prompt.disabled = Boolean(busy);
    sendButton.disabled = Boolean(busy);
  }

  function setWorkingStatus(textElement, ellipsisElement, message, working) {
    textElement.textContent = message;
    ellipsisElement.hidden = !working;
  }

  const api = { setBusy, setWorkingStatus };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.HomeHubChatUI = api;
})();

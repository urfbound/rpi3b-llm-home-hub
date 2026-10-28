(() => {
  function setBusy(prompt, sendButton, busy) {
    prompt.disabled = Boolean(busy);
    sendButton.disabled = Boolean(busy);
  }

  function beginSubmission(form, prompt, sendButton, FormDataConstructor = FormData) {
    const formData = new FormDataConstructor(form);
    setBusy(prompt, sendButton, true);
    return formData;
  }

  function setWorkingStatus(textElement, ellipsisElement, message, working) {
    textElement.textContent = message;
    ellipsisElement.hidden = !working;
  }

  const api = { beginSubmission, setBusy, setWorkingStatus };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.HomeHubChatUI = api;
})();

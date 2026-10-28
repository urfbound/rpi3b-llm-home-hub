const test = require('node:test');
const assert = require('node:assert/strict');
const { beginSubmission, setBusy, setWorkingStatus } = require('../homehub/static/chat-ui.js');

test('a queued or running request disables the prompt and send button', () => {
  const prompt = { disabled: false };
  const sendButton = { disabled: false };

  setBusy(prompt, sendButton, true);

  assert.equal(prompt.disabled, true);
  assert.equal(sendButton.disabled, true);
});

test('submission captures form data before disabling the prompt', () => {
  const prompt = { disabled: false };
  const sendButton = { disabled: false };
  const form = {};
  let capturedWhileEnabled = false;
  const expected = { prompt: 'What is the weather?' };
  class StubFormData {
    constructor(submittedForm) {
      assert.equal(submittedForm, form);
      capturedWhileEnabled = !prompt.disabled;
      return expected;
    }
  }

  const data = beginSubmission(form, prompt, sendButton, StubFormData);

  assert.equal(data, expected);
  assert.equal(capturedWhileEnabled, true);
  assert.equal(prompt.disabled, true);
  assert.equal(sendButton.disabled, true);
});

test('finishing or failing a request restores the prompt and send button', () => {
  const prompt = { disabled: true };
  const sendButton = { disabled: true };

  setBusy(prompt, sendButton, false);

  assert.equal(prompt.disabled, false);
  assert.equal(sendButton.disabled, false);
});

test('working status controls the animated ellipsis class accessibly', () => {
  const text = {
    textContent: '',
  };
  const ellipsis = { hidden: true };

  setWorkingStatus(text, ellipsis, 'Your local model is working', true);

  assert.equal(text.textContent, 'Your local model is working');
  assert.equal(ellipsis.hidden, false);
  setWorkingStatus(text, ellipsis, 'Response ready.', false);
  assert.equal(ellipsis.hidden, true);
});

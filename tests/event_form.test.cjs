const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('event description counter updates as the person types', () => {
  const template = fs.readFileSync(
    path.join(__dirname, '../homehub/templates/event_form.html'), 'utf8');
  const script = template.match(/<script>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(script);

  let onInput;
  const description = {
    value: '',
    addEventListener(event, handler) {
      if (event === 'input') onInput = handler;
    },
  };
  const count = {textContent: '256 characters remaining (256 maximum).'};
  const elements = {
    '[name="description"]': description,
    '[name="all_day"]': {checked: true, addEventListener() {}},
    '[name="title"]': {addEventListener() {}},
    '[name="recipe_title_auto"]': {value: '1'},
  };
  const ids = {
    'event-description-count': count,
    'event-time-fields': {hidden: true},
  };
  const document = {
    querySelector: selector => elements[selector],
    getElementById: id => ids[id],
  };
  vm.runInNewContext(
    script.replaceAll('{{ limits.EVENT_DESCRIPTION_MAXIMUM }}', '256'),
    {document});

  description.value = 'Dinner';
  onInput();
  assert.equal(count.textContent, '250 characters remaining (256 maximum).');

  description.value = 'x'.repeat(256);
  onInput();
  assert.equal(count.textContent, '0 characters remaining (256 maximum).');
});

# Browser i18n integration bridge

Browser integrations can subscribe to the live OpenAtelierUI i18n Svelte store
without depending on generated application chunk names:

```js
import { i18n } from '/static/openatelierui/i18n.js';

const unsubscribe = i18n.subscribe((instance) => {
  if (instance?.isInitialized) {
    instance.addResourceBundle('en-US', 'translation', { Example: 'Example' }, true, true);
    i18n.set(instance);
  }
});
```

The bridge can be imported before application initialization; subscription is
attached when the store becomes ready. The legacy path
`/_app/immutable/chunks/DKxluP3e.js` exports the same store as `i` for unchanged
v0.11.3 integrations. New integrations must use the stable path above.

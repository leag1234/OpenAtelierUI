// Stable OpenAtelierUI integration bridge.
// This proxy remains usable when an integration module loads just before the
// application publishes its Svelte store on window.OpenAtelierUI.
const currentStore = () => globalThis.OpenAtelierUI?.i18nStore;

export const i18n = {
	subscribe(run, invalidate) {
		let unsubscribe = () => {};
		let stopped = false;
		const attach = () => {
			if (stopped) return;
			const store = currentStore();
			if (store?.subscribe) unsubscribe = store.subscribe(run, invalidate);
		};
		if (currentStore()) attach();
		else globalThis.addEventListener?.('openatelierui:i18n-ready', attach, { once: true });
		return () => {
			stopped = true;
			globalThis.removeEventListener?.('openatelierui:i18n-ready', attach);
			unsubscribe();
		};
	},
	set(value) {
		const store = currentStore();
		if (!store?.set) throw new Error('OpenAtelierUI i18n store is not ready');
		store.set(value);
	},
	update(updater) {
		const store = currentStore();
		if (!store?.update) throw new Error('OpenAtelierUI i18n store is not ready');
		store.update(updater);
	}
};

// The short alias preserves the established v0.11.3 integration contract.
export { i18n as i };

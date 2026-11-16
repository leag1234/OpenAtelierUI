const nativeOnlyAttributes = [
	'aria-controls',
	'data-melt-dropdown-menu-trigger',
	'data-menu-trigger',
	'data-state',
	'tabindex'
];

export const nativeMenuTriggerContract = (node: HTMLElement, enabled = true) => {
	let active = enabled;

	const reconcile = () => {
		if (!active) return;

		for (const attribute of nativeOnlyAttributes) {
			if (node.hasAttribute(attribute)) node.removeAttribute(attribute);
		}

		if (node.getAttribute('aria-haspopup') !== 'menu') {
			node.setAttribute('aria-haspopup', 'menu');
		}
	};

	const observer = new MutationObserver(reconcile);
	observer.observe(node, { attributes: true });
	reconcile();

	return {
		update(nextEnabled = true) {
			active = nextEnabled;
			reconcile();
		},
		destroy() {
			observer.disconnect();
		}
	};
};

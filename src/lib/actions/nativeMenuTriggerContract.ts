const nativeOnlyAttributes = [
	'aria-controls',
	'data-melt-dropdown-menu-trigger',
	'data-menu-trigger',
	'data-state',
	'tabindex'
];

type NativeMenuTriggerContractOptions =
	| boolean
	| { enabled?: boolean; removeGeneratedId?: boolean; ariaHasPopup?: string };

const readOptions = (options: NativeMenuTriggerContractOptions) =>
	typeof options === 'boolean'
		? { enabled: options, removeGeneratedId: false, ariaHasPopup: 'menu' }
		: {
				enabled: options.enabled ?? true,
				removeGeneratedId: options.removeGeneratedId ?? false,
				ariaHasPopup: options.ariaHasPopup ?? 'menu'
			};

export const nativeMenuTriggerContract = (
	node: HTMLElement,
	options: NativeMenuTriggerContractOptions = true
) => {
	let { enabled: active, removeGeneratedId, ariaHasPopup } = readOptions(options);

	const reconcile = () => {
		if (!active) return;

		for (const attribute of nativeOnlyAttributes) {
			if (node.hasAttribute(attribute)) node.removeAttribute(attribute);
		}
		if (removeGeneratedId && node.hasAttribute('id')) node.removeAttribute('id');

		if (node.getAttribute('aria-haspopup') !== ariaHasPopup) {
			node.setAttribute('aria-haspopup', ariaHasPopup);
		}
	};

	const observer = new MutationObserver(reconcile);
	observer.observe(node, { attributes: true });
	reconcile();

	return {
		update(nextOptions: NativeMenuTriggerContractOptions = true) {
			({ enabled: active, removeGeneratedId, ariaHasPopup } = readOptions(nextOptions));
			reconcile();
		},
		destroy() {
			observer.disconnect();
		}
	};
};

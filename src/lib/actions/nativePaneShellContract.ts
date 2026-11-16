const paneGroupAttributes = ['id', 'data-direction', 'data-pane-group', 'data-pane-group-id'];
const paneAttributes = ['data-pane', 'data-pane-group-id', 'data-pane-id'];

const removeAttributes = (element: Element | null, attributes: string[]) => {
	if (!element) return;

	for (const attribute of attributes) {
		if (element.hasAttribute(attribute)) element.removeAttribute(attribute);
	}
};

export const nativePaneShellContract = (node: HTMLElement, enabled = true) => {
	let active = enabled;

	const reconcile = () => {
		if (!active) return;

		const group = node.querySelector(
			':scope > [data-pane-group], :scope > .w-full.h-full.flex.flex-col'
		);
		const pane = group?.querySelector(
			':scope > [data-pane], :scope > .relative.w-full.h-full.min-h-0.flex'
		);

		removeAttributes(group, paneGroupAttributes);
		removeAttributes(pane ?? null, paneAttributes);
	};

	const observer = new MutationObserver(reconcile);
	observer.observe(node, { attributes: true, childList: true, subtree: true });
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

// SPDX-License-Identifier: BSD-3-Clause

const FOCUSABLE_SELECTOR =
	'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe:not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])';

/**
 * Add a bounded focus boundary for an opt-in modal. The returned cleanup restores
 * every body's previous inert state and returns focus to the element that opened it.
 */
export function activateModalFocus(modal) {
	const doc = modal.ownerDocument;
	const previousFocus = doc.activeElement;
	const background = Array.from(doc.body.children).filter((element) => element !== modal);
	const inertState = background.map((element) => ({
		element,
		inertValue: element.getAttribute('inert')
	}));

	for (const { element } of inertState) {
		element.setAttribute('inert', '');
	}

	const focusable = () =>
		Array.from(modal.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
			(element) => !element.disabled && element.tabIndex !== -1
		);

	const focusInitial = () => {
		const initial = modal.querySelector('[data-modal-initial-focus]');
		const candidates = focusable();
		(initial || candidates[0] || modal).focus();
	};

	const onKeyDown = (event) => {
		if (event.key !== 'Tab') return;
		const candidates = focusable();
		if (candidates.length === 0) {
			event.preventDefault();
			modal.focus();
			return;
		}

		const first = candidates[0];
		const last = candidates[candidates.length - 1];
		const active = doc.activeElement;
		if (event.shiftKey && (active === first || !modal.contains(active))) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && (active === last || !modal.contains(active))) {
			event.preventDefault();
			first.focus();
		}
	};

	const onFocusIn = (event) => {
		if (!modal.contains(event.target)) focusInitial();
	};

	// Focus events inside the sandboxed iframe do not cross its browsing-context
	// boundary. Recover only when the iframe/tab sequence moves outside this modal.
	const onFocusOut = (event) => {
		if (!event.relatedTarget || !modal.contains(event.relatedTarget)) focusInitial();
	};

	modal.addEventListener('keydown', onKeyDown);
	modal.addEventListener('focusout', onFocusOut);
	doc.addEventListener('focusin', onFocusIn, true);
	focusInitial();

	return () => {
		modal.removeEventListener('keydown', onKeyDown);
		modal.removeEventListener('focusout', onFocusOut);
		doc.removeEventListener('focusin', onFocusIn, true);
		for (const { element, inertValue } of inertState) {
			if (inertValue === null) element.removeAttribute('inert');
			else element.setAttribute('inert', inertValue);
		}
		if (previousFocus && previousFocus.isConnected) previousFocus.focus();
	};
}

#!/usr/bin/env node
// Deterministic DOM-lifecycle probe for the small focus helper (no browser claim).
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const helperPath = path.join(__dirname, '../src/lib/utils/modalFocus.js');
const helperSource = fs.readFileSync(helperPath, 'utf8').replace('export function activateModalFocus', 'function activateModalFocus');
const activateModalFocus = new Function(`${helperSource}; return activateModalFocus;`)();

function element(doc, options = {}) {
	const attributes = new Set(options.inert ? ['inert'] : []);
	return {
		ownerDocument: doc,
		tabIndex: options.tabIndex === undefined ? 0 : options.tabIndex,
		disabled: false,
		isConnected: true,
		focused: 0,
		listeners: {},
		focus() {
			this.focused += 1;
			doc.activeElement = this;
		},
		addEventListener(name, callback) {
			this.listeners[name] = callback;
		},
		removeEventListener(name) {
			delete this.listeners[name];
		},
		hasAttribute(name) {
			return attributes.has(name);
		},
		setAttribute(name) {
			attributes.add(name);
		},
		removeAttribute(name) {
			attributes.delete(name);
		},
		contains(other) {
			return this === other || (this.children || []).indexOf(other) !== -1;
		},
		querySelector(selector) {
			return selector === '[data-modal-initial-focus]' ? this.initial : null;
		},
		querySelectorAll() {
			return this.focusables || [];
		}
	};
}

const documentListeners = {};
const document = {
	activeElement: null,
	addEventListener(name, callback) {
		documentListeners[name] = callback;
	},
	removeEventListener(name) {
		delete documentListeners[name];
	}
};
const opener = element(document);
const app = element(document, { inert: true });
const navigation = element(document);
const modal = element(document, { tabIndex: -1 });
const close = element(document);
const sourceLink = element(document);
modal.initial = close;
modal.focusables = [close, sourceLink];
modal.children = [close, sourceLink];
modal.ownerDocument = document;
document.body = { children: [app, navigation, modal] };
document.activeElement = opener;

const cleanup = activateModalFocus(modal);
assert.strictEqual(document.activeElement, close, 'opening focuses the marked close control');
assert.strictEqual(app.hasAttribute('inert'), true, 'background becomes inert');
assert.strictEqual(navigation.hasAttribute('inert'), true, 'other background siblings become inert');
assert.strictEqual(modal.listeners.keydown instanceof Function, true, 'Tab handler is attached');

let prevented = false;
modal.listeners.keydown({ key: 'Tab', shiftKey: true, preventDefault() { prevented = true; } });
assert.strictEqual(prevented, true, 'Shift+Tab from first control is contained');
assert.strictEqual(document.activeElement, sourceLink, 'Shift+Tab wraps to last control');
prevented = false;
modal.listeners.keydown({ key: 'Tab', shiftKey: false, preventDefault() { prevented = true; } });
assert.strictEqual(prevented, true, 'Tab from last control is contained');
assert.strictEqual(document.activeElement, close, 'Tab wraps to first control');

const outside = element(document);
documentListeners.focusin({ target: outside });
assert.strictEqual(document.activeElement, close, 'focus escaping to background is returned to modal');

cleanup();
assert.strictEqual(app.hasAttribute('inert'), true, 'pre-existing inert state is preserved');
assert.strictEqual(navigation.hasAttribute('inert'), false, 'temporary inert state is removed');
assert.strictEqual(modal.listeners.keydown, undefined, 'Tab handler is removed');
assert.strictEqual(documentListeners.focusin, undefined, 'focus guard is removed');
assert.strictEqual(document.activeElement, opener, 'closing restores focus to opener');
console.log('modal focus lifecycle: 14 assertions passed');

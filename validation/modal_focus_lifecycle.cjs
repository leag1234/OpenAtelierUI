#!/usr/bin/env node
// Deterministic DOM-lifecycle probe for the small focus helper (no browser claim).
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const helperPath = path.join(__dirname, '../src/lib/utils/modalFocus.js');
const helperSource = fs.readFileSync(helperPath, 'utf8').replace('export function activateModalFocus', 'function activateModalFocus');
const navigationSource = helperSource
	.replace('export function activateContainedNavigationFocus', 'function activateContainedNavigationFocus')
	.replace('export function scheduleContainedNavigationFocus', 'function scheduleContainedNavigationFocus');
const helpers = new Function(`${navigationSource}; return { activateModalFocus, activateContainedNavigationFocus, scheduleContainedNavigationFocus };`)();
const { activateModalFocus, activateContainedNavigationFocus, scheduleContainedNavigationFocus } = helpers;

function element(doc, options = {}) {
	const attributes = options.inert ? { inert: '' } : {};
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
			return Object.prototype.hasOwnProperty.call(attributes, name);
		},
		getAttribute(name) {
			return this.hasAttribute(name) ? attributes[name] : null;
		},
		setAttribute(name, value) {
			attributes[name] = value;
		},
		removeAttribute(name) {
			delete attributes[name];
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
const iframe = element(document);
modal.initial = close;
modal.focusables = [close, sourceLink, iframe];
modal.children = [close, sourceLink, iframe];
modal.ownerDocument = document;
document.body = { children: [app, navigation, modal] };
document.activeElement = opener;

const cleanup = activateModalFocus(modal);
assert.strictEqual(document.activeElement, close, 'opening focuses the marked close control');
assert.strictEqual(app.hasAttribute('inert'), true, 'background becomes inert');
assert.strictEqual(navigation.hasAttribute('inert'), true, 'other background siblings become inert');
assert.strictEqual(modal.listeners.keydown instanceof Function, true, 'Tab handler is attached');
assert.strictEqual(modal.listeners.focusout instanceof Function, true, 'iframe exit guard is attached');

let prevented = false;
modal.listeners.keydown({ key: 'Tab', shiftKey: true, preventDefault() { prevented = true; } });
assert.strictEqual(prevented, true, 'Shift+Tab from first control is contained');
assert.strictEqual(document.activeElement, iframe, 'Shift+Tab from close reaches the iframe');
prevented = false;
modal.listeners.keydown({ key: 'Tab', shiftKey: false, preventDefault() { prevented = true; } });
assert.strictEqual(prevented, true, 'Tab from last control is contained');
assert.strictEqual(document.activeElement, close, 'Tab wraps to first control');

const outside = element(document);
modal.listeners.focusout({ relatedTarget: outside });
assert.strictEqual(document.activeElement, close, 'native focus leaving the iframe returns to modal');
documentListeners.focusin({ target: outside });
assert.strictEqual(document.activeElement, close, 'focus escaping to background is returned to modal');

app.removeAttribute('inert');
cleanup();
assert.strictEqual(app.hasAttribute('inert'), true, 'pre-existing inert=true state is restored');
assert.strictEqual(navigation.hasAttribute('inert'), false, 'captured inert=false state is restored');
assert.strictEqual(modal.listeners.keydown, undefined, 'Tab handler is removed');
assert.strictEqual(modal.listeners.focusout, undefined, 'iframe exit guard is removed');
assert.strictEqual(documentListeners.focusin, undefined, 'focus guard is removed');
assert.strictEqual(document.activeElement, opener, 'closing restores focus to opener');
const navDoc = { activeElement: null };
const navOpener = element(navDoc);
const nav = element(navDoc, { tabIndex: -1 });
const closeNav = element(navDoc);
const navLink = element(navDoc);
nav.focusables = [closeNav, navLink];
nav.children = [closeNav, navLink];
navDoc.activeElement = navOpener;
let escaped = false;
const stopNavigation = activateContainedNavigationFocus(nav, closeNav, () => { escaped = true; });
assert.strictEqual(navDoc.activeElement, closeNav, 'opening mobile navigation focuses its named close control');
prevented = false;
nav.listeners.keydown({ key: 'Tab', shiftKey: true, preventDefault() { prevented = true; } });
assert.strictEqual(prevented, true, 'Shift+Tab stays inside mobile navigation');
assert.strictEqual(navDoc.activeElement, navLink, 'Shift+Tab from close wraps to final navigation item');
prevented = false;
nav.listeners.keydown({ key: 'Escape', preventDefault() { prevented = true; } });
assert.strictEqual(escaped && prevented, true, 'Escape requests closing the navigation');
stopNavigation();
assert.strictEqual(navDoc.activeElement, navOpener, 'closing restores the original visible toggle');
assert.strictEqual(nav.listeners.keydown, undefined, 'close removes mobile keyboard handling');
async function deferredNavigationLifecycle() {
	const openDocument = { activeElement: null };
	const opener = element(openDocument);
	const navContainer = element(openDocument, { tabIndex: -1 });
	const closeControl = element(openDocument);
	navContainer.focusables = [closeControl];
	navContainer.children = [closeControl];
	openDocument.activeElement = opener;
	let releaseFlush;
	const flush = new Promise((resolve) => { releaseFlush = resolve; });
	let active = true;
	const cancel = scheduleContainedNavigationFocus(
		navContainer,
		closeControl,
		() => {},
		() => flush,
		() => active
	);
	assert.strictEqual(openDocument.activeElement, opener, 'focus waits for the DOM flush');
	assert.strictEqual(navContainer.listeners.keydown, undefined, 'no trap attaches before the flush');
	releaseFlush();
	await flush;
	await Promise.resolve();
	assert.strictEqual(openDocument.activeElement, closeControl, 'post-flush activation focuses close');
	assert.strictEqual(typeof navContainer.listeners.keydown, 'function', 'post-flush trap attaches');
	cancel();
	assert.strictEqual(openDocument.activeElement, opener, 'close cleanup restores the opener');
	assert.strictEqual(navContainer.listeners.keydown, undefined, 'close cleanup removes the trap');

	const staleDocument = { activeElement: null };
	const staleOpener = element(staleDocument);
	const staleNav = element(staleDocument, { tabIndex: -1 });
	const staleClose = element(staleDocument);
	staleNav.focusables = [staleClose];
	staleNav.children = [staleClose];
	staleDocument.activeElement = staleOpener;
	let releaseStale;
	const staleFlush = new Promise((resolve) => { releaseStale = resolve; });
	let stillMobileAndOpen = true;
	scheduleContainedNavigationFocus(staleNav, staleClose, () => {}, () => staleFlush, () => stillMobileAndOpen);
	stillMobileAndOpen = false;
	releaseStale();
	await staleFlush;
	await Promise.resolve();
	assert.strictEqual(staleDocument.activeElement, staleOpener, 'desktop transition fences stale focus');
	assert.strictEqual(staleNav.listeners.keydown, undefined, 'stale activation attaches no handler');

	const rapidDocument = { activeElement: null };
	const rapidOpener = element(rapidDocument);
	const rapidNav = element(rapidDocument, { tabIndex: -1 });
	const rapidClose = element(rapidDocument);
	rapidNav.focusables = [rapidClose];
	rapidNav.children = [rapidClose];
	rapidDocument.activeElement = rapidOpener;
	let releaseOld;
	let releaseNew;
	const oldFlush = new Promise((resolve) => { releaseOld = resolve; });
	const newFlush = new Promise((resolve) => { releaseNew = resolve; });
	const cancelOld = scheduleContainedNavigationFocus(rapidNav, rapidClose, () => {}, () => oldFlush, () => true);
	cancelOld(); // Close the first opening before its Svelte update commits.
	const cancelNew = scheduleContainedNavigationFocus(rapidNav, rapidClose, () => {}, () => newFlush, () => true);
	releaseOld();
	await oldFlush;
	await Promise.resolve();
	assert.strictEqual(rapidDocument.activeElement, rapidOpener, 'closed opening cannot steal focus after reopen');
	assert.strictEqual(rapidNav.listeners.keydown, undefined, 'cancelled opening adds no stale handler');
	releaseNew();
	await newFlush;
	await Promise.resolve();
	assert.strictEqual(rapidDocument.activeElement, rapidClose, 'current reopen receives focus after its flush');
	cancelNew();
	assert.strictEqual(rapidDocument.activeElement, rapidOpener, 'new opening cleanup restores its opener');
}

deferredNavigationLifecycle().then(
	() => console.log('modal and mobile navigation focus lifecycle: 34 assertions passed'),
	(error) => { console.error(error); process.exitCode = 1; }
);

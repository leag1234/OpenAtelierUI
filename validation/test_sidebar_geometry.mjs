// SPDX-License-Identifier: BSD-3-Clause
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('app content consumes the space after the existing compact rail', () => {
	const layout = source('../src/routes/+layout.svelte');
	assert.match(layout, /<div class="min-w-0 w-full flex-1">/);
	assert.doesNotMatch(layout, /max-w-\[calc\(100%-4\.5rem\)\]/);
	const rail = source('../src/lib/components/app/AppSidebar.svelte');
	assert.match(rail, /min-w-\[4\.5rem\]/);
});

test('collapsed navigation is inert and has a keyboard-operable visible reopen control', () => {
	const sidebar = source('../src/lib/components/layout/Sidebar.svelte');
	const hiddenNav = sidebar.match(/id="sidebar"[\s\S]*?<div[\s\S]*?aria-hidden=\{!\$showSidebar\}[\s\S]*?inert=\{!\$showSidebar\}/);
	assert.ok(hiddenNav, 'collapsed navigation must be removed from focus and accessibility navigation');
	const rail = source('../src/lib/components/app/AppSidebar.svelte');
	assert.match(rail, /aria-label="Open navigation"[\s\S]*?aria-controls="sidebar"[\s\S]*?on:click=\{\(\) => showSidebar\.set\(true\)\}/);
});

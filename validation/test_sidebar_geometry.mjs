// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Gaël Duval
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { sidebarStateMarker } from '../src/lib/utils/sidebarState.js';

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
	assert.equal(sidebarStateMarker(false), undefined);
	assert.equal(sidebarStateMarker(true), 'true');
	assert.match(sidebar, /data-state=\{sidebarStateMarker\(\$showSidebar\)\}/);
	assert.match(sidebar, /'-translate-x-\[260px\] w-\[0px\]'/);
	const rail = source('../src/lib/components/app/AppSidebar.svelte');
	assert.match(rail, /aria-label="Open navigation"[\s\S]*?aria-controls="sidebar"[\s\S]*?on:click=\{\(\) => showSidebar\.set\(true\)\}/);
	const navbar = source('../src/lib/components/layout/Navbar.svelte');
	const toggle = navbar.slice(navbar.indexOf('id="sidebar-toggle-button"'), navbar.indexOf('</button>', navbar.indexOf('id="sidebar-toggle-button"')));
	assert.match(toggle, /aria-label="Toggle Sidebar"/);
	assert.match(toggle, /showSidebar\.set\(!\$showSidebar\)/);
});

test('mobile navigation names its close control and enables focus containment only while open', () => {
	const sidebar = source('../src/lib/components/layout/Sidebar.svelte');
	const navbar = source('../src/lib/components/layout/Navbar.svelte');
	assert.match(sidebar, /scheduleContainedNavigationFocus\([\s\S]*?tick,[\s\S]*?navigation\.isConnected/);
	assert.match(sidebar, /\$mobile &&[\s\S]*?\$showSidebar &&[\s\S]*?navElement &&[\s\S]*?closeNavigationButton/);
	assert.match(sidebar, /role=\{\$mobile && \$showSidebar \? 'dialog' : undefined\}/);
	assert.match(sidebar, /aria-modal=\{\$mobile && \$showSidebar \? 'true' : undefined\}/);
	assert.match(sidebar, /bind:this=\{closeNavigationButton\}[\s\S]*?aria-label="Close navigation"/);
	assert.match(sidebar, /\$mobile \|\| !\$showSidebar\)[\s\S]*?cleanup\(\)/);
	assert.match(sidebar, /onDestroy\(\(\) => \{\s*if \(mobileFocusCleanup\) \{\s*mobileFocusCleanup\(\);/);
	assert.match(navbar, /id="sidebar-toggle-button"[\s\S]*?aria-label="Toggle Sidebar"[\s\S]*?aria-controls="sidebar"[\s\S]*?aria-expanded=\{\$showSidebar\}/);
});

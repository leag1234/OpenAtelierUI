// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Gaël Duval

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { detachFileForRemoval } from '../src/lib/utils/file-removal.js';

const first = { id: 'first', type: 'file' };
const second = { id: 'second', type: 'file' };
const original = [first, second];

const detachedFirst = detachFileForRemoval(original, 0);
assert.deepEqual(detachedFirst.files, [second]);
assert.equal(detachedFirst.removed, first);
assert.deepEqual(original, [first, second]);

const detachedSecond = detachFileForRemoval(detachedFirst.files, 0);
assert.deepEqual(detachedSecond.files, []);
assert.equal(detachedSecond.removed, second);

for (const invalid of [-1, 2, 1.5, Number.NaN]) {
	const result = detachFileForRemoval(original, invalid);
	assert.equal(result.files, original);
	assert.equal(result.removed, null);
}

const source = fs.readFileSync(
	new URL('../src/lib/components/chat/MessageInput.svelte', import.meta.url),
	'utf8'
);
const handlerStart = source.indexOf('on:dismiss={async () => {');
const handlerEnd = source.indexOf('\n\t\t\t\t\t\t\t\t\t\t\t\t\t}', handlerStart);
assert.notEqual(handlerStart, -1);
assert.notEqual(handlerEnd, -1);
const handler = source.slice(handlerStart, handlerEnd);
assert.ok(handler.includes('files = detached.files;'));
assert.ok(handler.includes('await deleteFileById(localStorage.token, removed.id);'));
assert.ok(
	handler.indexOf('files = detached.files;') <
		handler.indexOf('await deleteFileById(localStorage.token, removed.id);')
);

console.log(JSON.stringify({ passed: true, rapid_removals: 2, invalid_indices: 4 }));

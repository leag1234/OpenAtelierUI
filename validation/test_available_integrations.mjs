// SPDX-License-Identifier: BSD-3-Clause
import test from 'node:test';
import assert from 'node:assert/strict';
import { availableIntegrations } from '../src/lib/utils/availableIntegrations.js';

test('configured built-in integrations remain discoverable with their permission', () => {
	assert.equal(availableIntegrations({ features: { enable_web_search: true } }, { role: 'admin' }), true);
	assert.equal(availableIntegrations({ features: { enable_web_search: true } }, { role: 'user', permissions: { features: { web_search: true } } }), true);
	assert.equal(availableIntegrations({ features: { enable_web_search: true } }, { role: 'user' }), false);
	assert.equal(availableIntegrations({ features: {} }, { role: 'admin' }), false);
});

test('reachable tools or servers expose the selector without an empty anonymous control', () => {
	assert.equal(availableIntegrations({}, { role: 'user' }, [{}]), true);
	assert.equal(availableIntegrations({}, { role: 'user' }, [], [{ id: 'tool' }], ['tool']), true);
	assert.equal(availableIntegrations({}, { role: 'user' }, [], [{ id: 'tool' }], []), false);
	assert.equal(availableIntegrations({}, null, [{}]), false);
});

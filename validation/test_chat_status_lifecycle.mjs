// SPDX-License-Identifier: BSD-3-Clause
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../src/lib/components/chat/Chat.svelte', import.meta.url), 'utf8');

function componentHelper(name) {
	const signature = `\tconst ${name} = (message) => {`;
	const start = source.indexOf(signature);
	assert.notEqual(start, -1, `${name} must remain wired in Chat.svelte`);
	const bodyStart = start + signature.length;
	const end = source.indexOf('\n\t};', bodyStart);
	assert.notEqual(end, -1, `${name} helper must have a complete body`);
	return new Function('message', source.slice(bodyStart, end));
}

test('completion clears pending status but preserves completed plugin and tool history', () => {
	const settle = componentHelper('settlePendingResponseStatuses');
	const message = {
		content: 'Completed continuation',
		status: { action: 'thinking', description: 'Thinking… (0.0s)', done: false },
		statusHistory: [
			{ action: 'plugin_call', description: 'Plugin finished', done: true },
			{ action: 'tool_call', description: 'Tool finished', done: true },
			{ action: 'thinking', description: 'Thinking… (0.0s)', done: false }
		]
	};

	settle(message);

	assert.equal(message.content, 'Completed continuation');
	assert.deepEqual(message.statusHistory, [
		{ action: 'plugin_call', description: 'Plugin finished', done: true },
		{ action: 'tool_call', description: 'Tool finished', done: true }
	]);
	assert.equal('status' in message, false);
});

test('continuing a stopped response removes only the stale stopped marker', () => {
	const clearStopped = componentHelper('clearStoppedResponseStatus');
	const message = {
		statusHistory: [
			{ action: 'response_stopped', description: 'Response stopped', done: true },
			{ action: 'plugin_call', description: 'Plugin finished', done: true }
		]
	};

	clearStopped(message);

	assert.deepEqual(message.statusHistory, [
		{ action: 'plugin_call', description: 'Plugin finished', done: true }
	]);

	const legacyMessage = { status: { action: 'response_stopped', description: 'Response stopped', done: true } };
	clearStopped(legacyMessage);
	assert.equal('status' in legacyMessage, false);
});

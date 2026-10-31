// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Gaël Duval

import assert from 'node:assert/strict';
import {
	chatDraftStorageKey,
	clearChatDraft,
	readChatDraft,
	saveChatDraft
} from '../src/lib/utils/chat-draft.js';

class MemoryStorage {
	constructor() {
		this.values = new Map();
	}
	getItem(key) {
		return this.values.has(key) ? this.values.get(key) : null;
	}
	setItem(key, value) {
		this.values.set(key, value);
	}
	removeItem(key) {
		this.values.delete(key);
	}
}

const storage = new MemoryStorage();
const aliceNew = { userId: 'alice', chatId: '' };
const aliceChat = { userId: 'alice', chatId: 'chat-1' };
const bobChat = { userId: 'bob', chatId: 'chat-1' };
const draft = {
	prompt: 'Keep this draft',
	files: [],
	selectedToolIds: ['tool-1', 42],
	webSearchEnabled: true,
	imageGenerationEnabled: false,
	codeInterpreterEnabled: true
};

assert.notEqual(chatDraftStorageKey(aliceNew), chatDraftStorageKey(aliceChat));
assert.notEqual(chatDraftStorageKey(aliceChat), chatDraftStorageKey(bobChat));
assert.equal(chatDraftStorageKey({ ...aliceChat, temporary: true }), null);
assert.equal(chatDraftStorageKey({ userId: '', chatId: 'chat-1' }), null);

assert.equal(saveChatDraft(storage, aliceNew, draft), true);
assert.deepEqual(readChatDraft(storage, aliceNew), {
	...draft,
	selectedToolIds: ['tool-1']
});
assert.equal(readChatDraft(storage, aliceChat), null);

assert.equal(saveChatDraft(storage, aliceChat, draft), true);
assert.equal(readChatDraft(storage, bobChat), null);
assert.equal(saveChatDraft(storage, { ...aliceChat, temporary: true }, draft), false);

storage.setItem(chatDraftStorageKey(bobChat), '{not-json');
assert.equal(readChatDraft(storage, bobChat), null);
storage.setItem(chatDraftStorageKey(bobChat), JSON.stringify({ prompt: 42, files: [] }));
assert.equal(readChatDraft(storage, bobChat), null);

assert.equal(saveChatDraft(storage, aliceChat, { ...draft, prompt: '' }), false);
assert.equal(readChatDraft(storage, aliceChat), null);
assert.equal(saveChatDraft(storage, aliceChat, { ...draft, prompt: '', files: [{ id: 'file-1' }] }), true);
assert.equal(readChatDraft(storage, aliceChat).files[0].id, 'file-1');
assert.equal(clearChatDraft(storage, aliceChat), true);
assert.equal(readChatDraft(storage, aliceChat), null);

console.log('chat draft tests passed');

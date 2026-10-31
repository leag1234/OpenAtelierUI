// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Gaël Duval

const DRAFT_PREFIX = 'openatelierui:chat-draft:v1';

const storageKey = ({ userId, chatId = '', temporary = false }) => {
	if (temporary || typeof userId !== 'string' || userId.trim() === '') return null;

	const owner = encodeURIComponent(userId.trim());
	const conversation = chatId ? `chat:${chatId}` : 'new';
	return `${DRAFT_PREFIX}:${owner}:${encodeURIComponent(conversation)}`;
};

const normalizeDraft = (value) => {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	if (typeof value.prompt !== 'string' || !Array.isArray(value.files)) return null;

	return {
		prompt: value.prompt,
		files: value.files,
		selectedToolIds: Array.isArray(value.selectedToolIds)
			? value.selectedToolIds.filter((id) => typeof id === 'string')
			: [],
		webSearchEnabled: value.webSearchEnabled === true,
		imageGenerationEnabled: value.imageGenerationEnabled === true,
		codeInterpreterEnabled: value.codeInterpreterEnabled === true
	};
};

export const readChatDraft = (storage, context) => {
	const key = storageKey(context);
	if (!key) return null;

	try {
		return normalizeDraft(JSON.parse(storage.getItem(key)));
	} catch {
		return null;
	}
};

export const saveChatDraft = (storage, context, value) => {
	const key = storageKey(context);
	if (!key) return false;

	const draft = normalizeDraft(value);
	try {
		if (!draft || (draft.prompt.length === 0 && draft.files.length === 0)) {
			storage.removeItem(key);
			return false;
		}
		storage.setItem(key, JSON.stringify(draft));
		return true;
	} catch {
		return false;
	}
};

export const clearChatDraft = (storage, context) => {
	const key = storageKey(context);
	if (!key) return false;

	try {
		storage.removeItem(key);
		return true;
	} catch {
		return false;
	}
};

export const chatDraftStorageKey = (context) => storageKey(context);

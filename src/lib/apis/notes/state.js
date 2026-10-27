export const editableText = (data) => {
	if (data && typeof data === 'object' && !Array.isArray(data)) {
		const keys = Object.keys(data);
		if (keys.length === 0) return { editable: true, text: '' };
		if (keys.length === 1 && keys[0] === 'content') {
			const content = data.content;
			if (
				content &&
				typeof content === 'object' &&
				!Array.isArray(content) &&
				Object.keys(content).length === 1 &&
				Object.hasOwn(content, 'md') &&
				typeof content.md === 'string'
			) {
				return { editable: true, text: content.md };
			}
		}
	}
	return { editable: false, text: '' };
};

export const editableNote = (note) => {
	const parsed = editableText(note?.data);
	return {
		...parsed,
		editable: parsed.editable && (!note || (Array.isArray(note.access_grants) && note.access_grants.length === 0))
	};
};

export const isDirty = (draft, baseline) =>
	!baseline || draft.title !== baseline.title || draft.text !== baseline.text;

export const actionsBlocked = ({ saving = false, opening = false, pinning = false } = {}) =>
	saving || opening || pinning;

export const saveStatus = (listRefreshed) =>
	listRefreshed ? 'Saved' : 'Saved. The note list could not be refreshed.';

export const notePayload = (title, text, note = null) => {
	if (note && !editableNote(note).editable) throw new Error('This note cannot be safely edited');
	return {
		title,
		data: { content: { md: text } },
		meta: note ? note.meta : null,
		access_grants: []
	};
};

export const resolveSave = (draft, saved, failed = false) =>
	failed
		? { ...draft }
		: {
				...draft,
				selected: saved,
				baseline: { title: saved.title, text: draft.text },
				status: 'saved'
			};

export const safeNoteFilename = () => 'private-note.json';

const originalResponses = new WeakMap();
const MAX_RETAINED_RESPONSE_BYTES = 65536;
const MAX_NOTE_READER_BYTES = 65536;
const MAX_NOTE_READER_PAGES = 32;
const MAX_NOTE_PAGE_TITLE_BYTES = 512;

const validUnicode = (value) => {
	for (let index = 0; index < value.length; index++) {
		const unit = value.charCodeAt(index);
		if (unit >= 0xd800 && unit <= 0xdbff) {
			const next = value.charCodeAt(++index);
			if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
		} else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
	}
	return true;
};

const boundedText = (value) => {
	if (typeof value !== 'string' || value.length > MAX_NOTE_READER_BYTES || !validUnicode(value)) return false;
	return new TextEncoder().encode(value).byteLength <= MAX_NOTE_READER_BYTES;
};

// Read only saved Markdown text. Rich HTML, JSON editor trees, metadata, and
// revision data are deliberately ignored and remain available in the original.
export const readableNotePages = (note) => {
	const unavailable = () => { throw new Error('This note has no supported text preview. Download the original JSON to preserve all content.'); };
	const data = note?.data;
	if (!data || typeof data !== 'object' || Array.isArray(data)) return unavailable();
	const pages = [];
	if (Object.hasOwn(data, 'pages')) {
		if (!Array.isArray(data.pages) || data.pages.length < 1 || data.pages.length > MAX_NOTE_READER_PAGES) return unavailable();
		let totalBytes = 0;
		for (const page of data.pages) {
			const pageTitle = page?.title;
			const markdown = page?.content?.md;
			if (typeof pageTitle !== 'string' || pageTitle.length > MAX_NOTE_PAGE_TITLE_BYTES || !pageTitle.trim() || !boundedText(pageTitle) || new TextEncoder().encode(pageTitle).byteLength > MAX_NOTE_PAGE_TITLE_BYTES || !boundedText(markdown)) return unavailable();
			totalBytes += new TextEncoder().encode(pageTitle).byteLength + new TextEncoder().encode(markdown).byteLength;
			if (totalBytes > MAX_NOTE_READER_BYTES) return unavailable();
			pages.push({ title: pageTitle, text: markdown });
		}
		return pages;
	}
	const markdown = data.content?.md;
	if (!boundedText(markdown)) return unavailable();
	return [{ title: typeof note.title === 'string' && note.title.trim() ? note.title : 'Note', text: markdown }];
};

export const plainTextCopyDraft = (note) => {
	const pages = readableNotePages(note);
	const text = pages.length === 1
		? pages[0].text
		: pages.map((page) => `## ${page.title}\n\n${page.text}`).join('\n\n---\n\n');
	if (!boundedText(text)) throw new Error('This note is too large for a plain-text copy.');
	const originalTitle = typeof note?.title === 'string' && note.title.trim() ? note.title : 'Untitled note';
	const title = `Copy of ${originalTitle}`;
	if (!boundedText(title) || new TextEncoder().encode(title).byteLength > MAX_NOTE_PAGE_TITLE_BYTES) throw new Error('This note title is too long to make a plain-text copy.');
	return { title, text };
};

export const parseRetainedNoteResponse = (raw) => {
	const value = JSON.parse(raw);
	if (value && typeof value === 'object' && !Array.isArray(value) && raw.length <= MAX_RETAINED_RESPONSE_BYTES &&
		new TextEncoder().encode(raw).byteLength <= MAX_RETAINED_RESPONSE_BYTES) {
		originalResponses.set(value, raw);
	}
	return value;
};

export const originalNoteResponse = (note) =>
	note && typeof note === 'object' ? originalResponses.get(note) ?? null : null;

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

export const focusTrapTargetIndex = (activeIndex, count, backward = false) => {
	if (count < 1) return -1;
	if (activeIndex < 0) return backward ? count - 1 : 0;
	if (backward && activeIndex === 0) return count - 1;
	if (!backward && activeIndex === count - 1) return 0;
	return null;
};

export const saveStatus = (listRefreshed) =>
	listRefreshed ? 'Saved' : 'Saved. The note list could not be refreshed.';

export const notePayload = (title, text, note = null, textChanged = true) => {
	if (note && !editableNote(note).editable) throw new Error('This note cannot be safely edited');
	return note
		? { title, ...(textChanged ? { data: { content: { md: text } } } : {}), access_grants: [] }
		: { title, data: { content: { md: text } }, meta: null, access_grants: [] };
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

export const formatNoteDate = (value) => {
	if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isInteger(value)) return 'Unknown date';
	const seconds = value >= 946684800 && value <= 4102444800;
	const nanoseconds = value >= 946684800000000000 && value <= 4102444800000000000;
	if (!seconds && !nanoseconds) return 'Unknown date';
	const date = new Date(seconds ? value * 1000 : value / 1e6);
	return Number.isNaN(date.getTime()) ? 'Unknown date' : date.toLocaleDateString();
};

import assert from 'node:assert/strict';
import test from 'node:test';
import { actionsBlocked, editableNote, editableText, focusTrapTargetIndex, formatNoteDate, isDirty, notePayload, originalNoteResponse, parseRetainedNoteResponse, resolveSave, safeNoteFilename, saveStatus } from '../src/lib/apis/notes/state.js';

test('note dates support measured nanoseconds and historical seconds only', () => {
	const seconds = 1792949159;
	assert.equal(formatNoteDate(seconds), new Date(seconds * 1000).toLocaleDateString());
	assert.equal(formatNoteDate(seconds * 1e9), new Date((seconds * 1e9) / 1e6).toLocaleDateString());
	for (const value of [null, '1792949159', -1, 0, 1792949159000, NaN, Infinity, 9000000000000000000]) {
		assert.equal(formatNoteDate(value), 'Unknown date');
	}
});

test('only the measured plain-text note shapes are editable', () => {
	assert.deepEqual(editableText({}), { editable: true, text: '' });
	assert.deepEqual(editableText({ content: { md: 'Venue: Community workshop' } }), {
		editable: true,
		text: 'Venue: Community workshop'
	});
	for (const data of [null, [], { content: { md: 'x', html: '<b>x</b>' } }, { other: 'keep' }, { content: '<b>x</b>' }]) {
		assert.equal(editableText(data).editable, false);
	}
	assert.equal(editableNote({ data: {}, access_grants: [] }).editable, true);
	assert.equal(editableNote({ data: {}, access_grants: [{ permission: 'read' }] }).editable, false);
});

test('draft state distinguishes unchanged, changed, and new notes', () => {
	const baseline = { title: 'Weekend plan', text: 'Book the community room.' };
	assert.equal(isDirty(baseline, baseline), false);
	assert.equal(isDirty({ ...baseline, text: 'Ask about room access.' }, baseline), true);
	assert.equal(isDirty({ title: '', text: '' }, null), true);
});

test('opening and pinning block conflicting selections and writes', () => {
	assert.equal(actionsBlocked({ opening: true }), true);
	assert.equal(actionsBlocked({ pinning: true }), true);
	assert.equal(actionsBlocked({ saving: true }), true);
	assert.equal(actionsBlocked({}), false);
});

test('unsaved dialog focus stays inside and wraps at both Tab boundaries', () => {
	assert.equal(focusTrapTargetIndex(-1, 3), 0);
	assert.equal(focusTrapTargetIndex(-1, 3, true), 2);
	assert.equal(focusTrapTargetIndex(0, 3, true), 2);
	assert.equal(focusTrapTargetIndex(2, 3), 0);
	assert.equal(focusTrapTargetIndex(1, 3), null);
	assert.equal(focusTrapTargetIndex(-1, 0), -1);
});

test('a saved note stays distinguished from a failed list refresh', () => {
	assert.equal(saveStatus(true), 'Saved');
	assert.match(saveStatus(false), /^Saved\./);
	assert.match(saveStatus(false), /list could not be refreshed/);
});

test('existing updates send only changed fields while new notes retain create defaults', () => {
	assert.deepEqual(notePayload('Workshop plan', 'Bring printed maps.', {
		data: {}, meta: { source: 'member' }, access_grants: []
	}, false), { title: 'Workshop plan', access_grants: [] });
	assert.deepEqual(notePayload('Workshop plan', '', { data: {}, meta: null, access_grants: [] }, true), {
		title: 'Workshop plan', data: { content: { md: '' } }, access_grants: []
	});
	assert.deepEqual(notePayload('Workshop plan', 'Bring printed maps.'), {
		title: 'Workshop plan', data: { content: { md: 'Bring printed maps.' } }, meta: null, access_grants: []
	});
	assert.throws(() => notePayload('Keep unchanged', 'No overwrite', {
		data: { other: 'opaque' }, meta: { keep: true }, access_grants: []
	}));
	assert.throws(() => notePayload('Keep private', 'No sharing reset', {
		data: {}, meta: null, access_grants: [{ permission: 'read' }]
	}));
	assert.equal(safeNoteFilename(), 'private-note.json');
});

test('parsed note responses retain exact bounded JSON text for lossless download', () => {
	const raw = '{"id":"note-1","title":"Workshop plan","created_at":1792949159000000123,"meta":{"counter":90071992547409931234},"data":{"content":{"md":"Keep"}},"access_grants":[]}';
	const parsed = parseRetainedNoteResponse(raw);
	assert.equal(originalNoteResponse(parsed), raw);
	assert.notEqual(JSON.stringify(parsed), raw);
	assert.deepEqual(notePayload('Renamed', 'Keep', parsed, false), { title: 'Renamed', access_grants: [] });
	assert.equal(originalNoteResponse({ ...parsed }), null);
	const large = `{"data":{"content":{"md":"${'x'.repeat(65536)}"}}}`;
	assert.equal(originalNoteResponse(parseRetainedNoteResponse(large)), null);
});

test('failed save retains the entire draft while success advances its baseline', () => {
	const draft = {
		selected: { id: 'n1', meta: { source: 'member' } },
		title: 'Workshop plan',
		text: 'Keep this draft after a failed request.',
		baseline: { title: 'Old title', text: 'Old text' },
		editable: true
	};
	const failed = resolveSave(draft, null, true);
	assert.deepEqual(failed, draft);
	assert.equal(isDirty({ title: failed.title, text: failed.text }, failed.baseline), true);
	const saved = resolveSave(draft, { id: 'n1', title: draft.title, meta: draft.selected.meta }, false);
	assert.deepEqual(saved.baseline, { title: draft.title, text: draft.text });
	assert.equal(saved.selected.meta, draft.selected.meta);
	assert.equal(isDirty({ title: draft.title, text: draft.text }, saved.baseline), false);
});

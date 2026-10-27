import assert from 'node:assert/strict';
import test from 'node:test';
import { editableNote, editableText, isDirty, notePayload, resolveSave, safeNoteFilename } from '../src/lib/apis/notes/state.js';

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

test('save payload preserves metadata including explicit null and keeps text plain', () => {
	assert.deepEqual(notePayload('Workshop plan', 'Bring printed maps.', {
		data: {}, meta: { source: 'member' }, access_grants: []
	}), {
		title: 'Workshop plan',
		data: { content: { md: 'Bring printed maps.' } },
		meta: { source: 'member' },
		access_grants: []
	});
	assert.deepEqual(notePayload('Workshop plan', 'Revised.', { data: {}, meta: null, access_grants: [] }).meta, null);
	assert.throws(() => notePayload('Keep unchanged', 'No overwrite', {
		data: { other: 'opaque' }, meta: { keep: true }, access_grants: []
	}));
	assert.throws(() => notePayload('Keep private', 'No sharing reset', {
		data: {}, meta: null, access_grants: [{ permission: 'read' }]
	}));
	assert.equal(safeNoteFilename(), 'private-note.json');
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

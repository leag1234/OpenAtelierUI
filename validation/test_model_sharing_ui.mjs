import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/lib/apis/models/sharing.js', import.meta.url), 'utf8');
const sharing = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('model visibility maps only to private, public-read, or selected-user grants', () => {
	assert.deepEqual(sharing.buildModelReadGrants('private'), []);
	assert.deepEqual(sharing.buildModelReadGrants('public'), [
		{ principal_type: 'user', principal_id: '*', permission: 'read' }
	]);
	assert.deepEqual(sharing.buildModelReadGrants('users', 'member-a\nmember-b'), [
		{ principal_type: 'user', principal_id: 'member-a', permission: 'read' },
		{ principal_type: 'user', principal_id: 'member-b', permission: 'read' }
	]);
});

test('parser preserves known grants and refuses write access, duplicates and unknown fields', () => {
	const parsed = sharing.parseModelReadGrants({ id: 'm1', access_grants: [
		{ id: 7, resource_type: 'model', resource_id: 'm1', principal_type: 'user', principal_id: 'member-a', permission: 'read', created_at: 'now' }
	] });
	assert.deepEqual(parsed, {
		supported: true,
		visibility: 'users',
		userIds: ['member-a'],
		groupIds: [],
		accessControl: { read: { group_ids: [], user_ids: [] }, write: { group_ids: [], user_ids: [] } }
	});
	for (const grant of [
		{ principal_type: 'user', principal_id: 'u1', permission: 'write' },
		{ principal_type: 'user', principal_id: 'u1', permission: 'read', extra: true }
	]) assert.equal(sharing.parseModelReadGrants({ access_grants: [grant] }).supported, false);
	assert.equal(sharing.parseModelReadGrants({ access_grants: [
		{ principal_type: 'user', principal_id: 'u1', permission: 'read' },
		{ principal_type: 'user', principal_id: 'u1', permission: 'read' }
	] }).supported, false);
	assert.equal(sharing.parseModelReadGrants({ access_grants: [
		{ principal_type: 'user', principal_id: 'u1\nbad', permission: 'read' }
	] }).supported, false);
	assert.equal(sharing.parseModelReadGrants({ access_grants: [
		{ principal_type: 'group', principal_id: '*', permission: 'read' }
	] }).supported, false);
	assert.deepEqual(sharing.parseModelReadGrants({ access_control: {} }), {
		supported: true, visibility: 'private', userIds: [], groupIds: [],
		accessControl: { read: { group_ids: [], user_ids: [] }, write: { group_ids: [], user_ids: [] } }
	});
	assert.deepEqual(sharing.parseModelReadGrants({ access_control: null }), {
		supported: true, visibility: 'public', userIds: [], groupIds: [], accessControl: null
	});
});

test('model group read grants stay named and round-trip; write grants remain unsupported', async () => {
	const parsed = sharing.parseModelReadGrants({ id: 'm1', access_grants: [
		{ principal_type: 'group', principal_id: 'workshop', permission: 'read', resource_type: 'model', resource_id: 'm1' },
		{ principal_type: 'user', principal_id: 'user-a', permission: 'read', resource_type: 'model', resource_id: 'm1' }
	] });
	assert.equal(parsed.supported, true);
	assert.deepEqual(parsed.groupIds, ['workshop']);
	assert.deepEqual(sharing.buildReadGrantsFromParsed(parsed), [
		{ principal_type: 'group', principal_id: 'workshop', permission: 'read' },
		{ principal_type: 'user', principal_id: 'user-a', permission: 'read' }
	]);
	assert.deepEqual(sharing.buildModelAccessReadGrants(parsed.accessControl, 'user-a'), [
		{ principal_type: 'group', principal_id: 'workshop', permission: 'read' },
		{ principal_type: 'user', principal_id: 'user-a', permission: 'read' }
	]);
	const unchangedCalls = [];
	const unchanged = sharing.buildReadGrantsFromParsed(parsed);
	await sharing.saveModelWithSharing('m1', { name: 'Edited', access_grants: unchanged }, unchanged,
		async () => ({ id: 'm1' }), async () => { unchangedCalls.push('access update'); return { id: 'm1' }; });
	assert.deepEqual(unchangedCalls, []);
	assert.throws(() => sharing.buildModelAccessReadGrants({
		read: { group_ids: ['*'], user_ids: [] }, write: { group_ids: [], user_ids: [] }
	}, ''));
	assert.equal(sharing.parseModelReadGrants({ access_grants: [
		{ principal_type: 'user', principal_id: 'user-a', permission: 'read' }
	], access_control: { write: { group_ids: ['g1'], user_ids: [] } } }).supported, false);
	assert.equal(sharing.parseResourceReadGrants({ access_grants: [
		{ principal_type: 'group', principal_id: 'g1', permission: 'read', resource_type: 'tool' }
	] }, 'tool').supported, false);
});

test('selected IDs reject duplicates, wildcard mixing, control characters and empty selection', () => {
	for (const input of ['', 'u1\nu1', '*\nu1', 'u1\nbad\u0001id']) {
		assert.throws(() => sharing.buildModelReadGrants('users', input));
	}
});

test('create payload removes legacy ACL but retains explicit grants', () => {
	const payload = sharing.modelCreatePayload({ name: 'Example', access_control: { read: {} }, access_grants: [] });
	assert.deepEqual(payload, { name: 'Example', access_grants: [] });
});

test('edit updates metadata first and calls dedicated ACL route only for an actual change', async () => {
	const calls = [];
	const updateDetails = async (_id, details) => { calls.push(['details', details]); return { id: 'm1' }; };
	const updateAccess = async (_id, grants) => { calls.push(['access', grants]); return { id: 'm1' }; };
	const current = [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
	let result = await sharing.saveModelWithSharing('m1', { name: 'new', access_control: {}, access_grants: current }, current, updateDetails, updateAccess);
	assert.equal(result.status, 'saved');
	assert.equal(calls.length, 1);
	assert.deepEqual(calls[0], ['details', { name: 'new' }]);

	calls.length = 0;
	result = await sharing.saveModelWithSharing('m1', { name: 'new', access_control: {}, access_grants: [] }, current, updateDetails, updateAccess);
	assert.equal(result.status, 'saved');
	assert.deepEqual(calls.map(([name]) => name), ['details', 'access']);
});

test('partial sharing failure is reported after saved details without a false success', async () => {
	const result = await sharing.saveModelWithSharing(
		'm1', { name: 'new', access_grants: [] }, [{ principal_type: 'user', principal_id: '*', permission: 'read' }],
		async () => ({ id: 'm1' }), async () => { throw new Error('private server detail'); }
	);
	assert.equal(result.status, 'sharing_failed');
	assert.deepEqual(result.model, { id: 'm1' });
	const failedDetails = await sharing.saveModelWithSharing(
		'm1', { name: 'new', access_grants: [] }, [], async () => null,
		async () => { throw new Error('ACL must not run after a failed details request'); }
	);
	assert.equal(failedDetails.status, 'details_failed');
});

test('dedicated ACL request uses fixed endpoint and explicit JSON grants, with sanitized refusal', async () => {
	let request;
	await sharing.requestModelAccessUpdate(async (url, options) => {
		request = { url, options };
		return { ok: true, json: async () => ({ id: 'm1' }) };
	}, '/api', 'token', 'm1', []);
	assert.equal(request.url, '/api/models/model/access/update');
	assert.equal(request.options.method, 'POST');
	assert.equal(request.options.headers.authorization, 'Bearer token');
	assert.deepEqual(JSON.parse(request.options.body), { id: 'm1', access_grants: [] });
	await assert.rejects(() => sharing.requestModelAccessUpdate(async () => ({
		ok: false, json: async () => ({ detail: 'server-private-detail' })
	}), '/api', 'token', 'm1', []), /Access grants could not be updated/);
});

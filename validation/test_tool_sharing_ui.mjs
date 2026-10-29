import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/lib/apis/models/sharing.js', import.meta.url), 'utf8');
const sharing = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('tool editor reads user and group grants and refuses write or unrelated forms', async () => {
	assert.deepEqual(sharing.buildResourceReadGrants('private'), []);
	assert.deepEqual(sharing.parseResourceReadGrants({ id: 'weather', access_grants: [
		{ principal_type: 'user', principal_id: '*', permission: 'read', resource_type: 'tool', resource_id: 'weather' }
	] }, 'tool'), { supported: true, visibility: 'public', userIds: [], groupIds: [], accessControl: null });
	const groupGrants = [{ principal_type: 'group', principal_id: 'g1', permission: 'read', resource_type: 'tool', resource_id: 'weather' }];
	const parsedGroups = sharing.parseResourceReadGrants({ id: 'weather', access_grants: groupGrants }, 'tool', true);
	assert.equal(parsedGroups.supported, true);
	assert.deepEqual(parsedGroups.groupIds, ['g1']);
	assert.deepEqual(sharing.buildResourceAccessReadGrants(parsedGroups.accessControl, ''), [
		{ principal_type: 'group', principal_id: 'g1', permission: 'read' }
	]);
	const baseline = sharing.buildReadGrantsFromParsed(parsedGroups);
	let aclCalls = 0;
	await sharing.saveResourceWithSharing('weather', { name: 'Edited', access_grants: baseline }, baseline,
		async () => ({ id: 'weather' }), async () => { aclCalls++; return { id: 'weather' }; });
	assert.equal(aclCalls, 0);
	assert.equal(sharing.parseResourceReadGrants({ id: 'weather', access_grants: groupGrants }, 'tool').supported, false);
	assert.equal(sharing.parseResourceReadGrants({ id: 'weather', access_grants: [
		{ principal_type: 'user', principal_id: 'u1', permission: 'write', resource_type: 'tool' }
	] }, 'tool').supported, false);
	assert.equal(sharing.parseResourceReadGrants({ id: 'weather', access_grants: groupGrants,
		access_control: { write: { group_ids: ['g2'], user_ids: [] } }
	}, 'tool', true).supported, false);
	assert.equal(sharing.parseResourceReadGrants({ id: 'weather', access_grants: [
		{ principal_type: 'group', principal_id: 'g1', permission: 'read', resource_type: 'tool' },
		{ principal_type: 'user', principal_id: 'u1', permission: 'write', resource_type: 'tool' }
	] }, 'tool', true).supported, false);
	assert.equal(sharing.parseResourceReadGrants({ id: 'weather', access_grants: [
		{ principal_type: 'user', principal_id: '*', permission: 'read', resource_type: 'model' }
	] }, 'tool').supported, false);
});

test('public selection ignores hidden user-ID draft but private selection restores it', () => {
	const draft = 'member-a\nmember-b';
	assert.deepEqual(sharing.buildResourceAccessReadGrants(null, draft), [
		{ principal_type: 'user', principal_id: '*', permission: 'read' }
	]);
	assert.deepEqual(sharing.buildResourceAccessReadGrants(null, 'legacy\u0001draft'), [
		{ principal_type: 'user', principal_id: '*', permission: 'read' }
	]);
	assert.deepEqual(sharing.buildResourceAccessReadGrants({
		read: { group_ids: [], user_ids: [] }, write: { group_ids: [], user_ids: [] }
	}, draft), [
		{ principal_type: 'user', principal_id: 'member-a', permission: 'read' },
		{ principal_type: 'user', principal_id: 'member-b', permission: 'read' }
	]);
});

test('tool ACL request serializes only explicit grants to the dedicated endpoint', async () => {
	let call;
	await sharing.requestToolAccessUpdate(async (url, options) => {
		call = { url, options };
		return { ok: true, json: async () => ({ id: 'tool/one' }) };
	}, '/api', 'synthetic-token', 'tool/one', sharing.buildResourceReadGrants('users', 'user-a'));
	assert.equal(call.url, '/api/tools/id/tool%2Fone/access/update');
	assert.equal(call.options.method, 'POST');
	assert.equal(call.options.headers.authorization, 'Bearer synthetic-token');
	assert.deepEqual(JSON.parse(call.options.body), {
		id: 'tool/one',
		access_grants: [{ principal_type: 'user', principal_id: 'user-a', permission: 'read' }]
	});
});

test('tool detail edits preserve unchanged sharing and apply changes only after details succeed', async () => {
	const current = [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
	const calls = [];
	const details = async (_id, body) => { calls.push(['details', body]); return { id: 'weather' }; };
	const access = async (_id, grants) => { calls.push(['access', grants]); return { id: 'weather', access_grants: grants }; };
	let result = await sharing.saveResourceWithSharing('weather', {
		name: 'Weather', content: 'synthetic inert fixture', access_control: { ignored: true }, access_grants: current
	}, current, details, access);
	assert.equal(result.status, 'saved');
	assert.deepEqual(calls.map(([kind]) => kind), ['details']);
	assert.deepEqual(calls[0][1], { name: 'Weather', content: 'synthetic inert fixture' });
	calls.length = 0;
	const currentGroup = [{ principal_type: 'group', principal_id: 'g1', permission: 'read' }];
	result = await sharing.saveResourceWithSharing('weather', {
		name: 'Weather', access_grants: currentGroup
	}, currentGroup, details, access);
	assert.equal(result.status, 'saved');
	assert.deepEqual(calls.map(([kind]) => kind), ['details']);

	calls.length = 0;
	result = await sharing.saveResourceWithSharing('weather', {
		name: 'Weather', content: 'synthetic inert fixture', access_grants: []
	}, current, details, access);
	assert.equal(result.status, 'saved');
	assert.deepEqual(calls.map(([kind]) => kind), ['details', 'access']);
	assert.deepEqual(result.model.access_grants, []);
	calls.length = 0;
	result = await sharing.saveResourceWithSharing('weather', { name: 'Weather' }, undefined, details, access);
	assert.equal(result.status, 'saved');
	assert.deepEqual(calls.map(([kind]) => kind), ['details']);
});

test('tool sharing partial failure is actionable and never reports full success', async () => {
	let accessCalls = 0;
	const result = await sharing.saveResourceWithSharing('weather', {
		name: 'Updated', access_grants: []
	}, [{ principal_type: 'user', principal_id: '*', permission: 'read' }],
	async () => ({ id: 'weather' }), async () => { accessCalls++; throw new Error('private response'); });
	assert.equal(accessCalls, 1);
	assert.equal(result.status, 'sharing_failed');
	assert.deepEqual(result.model, { id: 'weather' });
});

test('failed ACL response exposes no response body details', async () => {
	await assert.rejects(() => sharing.requestToolAccessUpdate(async () => ({
		ok: false, json: async () => ({ detail: 'synthetic-private-detail' })
	}), '/api', 'token', 'weather', []), (error) => {
		assert.equal(error.message, 'Access grants could not be updated.');
		return true;
	});
});

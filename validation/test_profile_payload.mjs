// SPDX-License-Identifier: BSD-3-Clause
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
	buildUserProfileUpdatePayload,
	exposedProfileFields,
	hydrateSignedInProfile,
	supportedProfileValues
} from '../src/lib/apis/auths/profilePayload.js';

test('fields absent from session stay hidden and absent from the update payload', () => {
	const enabled = exposedProfileFields({ id: 'synthetic-user', name: 'Community member' });
	assert.deepEqual(enabled, { bio: false, gender: false, date_of_birth: false });
	const selected = supportedProfileValues(enabled, {
		bio: 'should not be sent',
		gender: 'should not be sent',
		date_of_birth: '2000-01-02'
	});
	assert.deepEqual(selected, {});
	assert.deepEqual(buildUserProfileUpdatePayload('Community member', '/user.png', selected), {
		name: 'Community member',
		profile_image_url: '/user.png'
	});
});

test('present nullable fields preserve values and send empty controls as explicit clears', () => {
	const enabled = exposedProfileFields({ bio: null, gender: 'Community', date_of_birth: null });
	assert.deepEqual(enabled, { bio: true, gender: true, date_of_birth: true });
	const selected = supportedProfileValues(enabled, {
		bio: '',
		gender: 'Community',
		date_of_birth: '2001-02-03'
	});
	assert.deepEqual(selected, { bio: '', gender: 'Community', date_of_birth: '2001-02-03' });
	assert.deepEqual(buildUserProfileUpdatePayload('Member', 'avatar', selected), {
		name: 'Member',
		profile_image_url: 'avatar',
		bio: null,
		gender: 'Community',
		date_of_birth: '2001-02-03'
	});
});

test('undefined optional values are omitted while invalid values fail before fetch construction', () => {
	assert.deepEqual(buildUserProfileUpdatePayload('Member', 'avatar', {
		bio: undefined,
		gender: null
	}), { name: 'Member', profile_image_url: 'avatar', gender: null });
	assert.throws(
		() => buildUserProfileUpdatePayload('Member', 'avatar', { bio: 42 }),
		{ name: 'TypeError', message: 'Profile field values must be strings or null' }
	);
	const auths = readFileSync(new URL('../src/lib/apis/auths/index.ts', import.meta.url), 'utf8');
	assert.match(auths, /body:\s*JSON\.stringify\(buildUserProfileUpdatePayload\(name, profileImageUrl, profileFields\)\)/);
});

test('successful sign-in hydrates authoritative optional values while retaining the original session', async () => {
	const initial = { id: 'member-1', name: 'Community member', token: 'synthetic-token' };
	const stored = [];
	const result = await hydrateSignedInProfile(
		initial,
		async (token) => {
			assert.equal(token, 'synthetic-token');
			return { id: 'member-1', bio: 'Workshop member', gender: null, date_of_birth: '1990-04-05' };
		},
		async (value) => stored.push(value)
	);
	assert.deepEqual(stored, [initial, {
		...initial,
		bio: 'Workshop member',
		gender: null,
		date_of_birth: '1990-04-05'
	}]);
	assert.equal(result.token, initial.token);
	assert.equal(result.name, initial.name);
	assert.equal(result.bio, 'Workshop member');
});

test('legacy session responses with absent optional fields keep the original signed-in state', async () => {
	const initial = { id: 'member-2', name: 'Community member', token: 'legacy-token' };
	const stored = [];
	const result = await hydrateSignedInProfile(
		initial,
		async () => ({ id: 'member-2', name: 'Community member' }),
		async (value) => stored.push(value)
	);
	assert.equal(result, initial);
	assert.deepEqual(stored, [initial]);
});

test('optional session refresh failure keeps the successful sign-in usable', async () => {
	const initial = { id: 'member-3', name: 'Community member', token: 'valid-token' };
	const stored = [];
	const result = await hydrateSignedInProfile(
		initial,
		async () => { throw new Error('synthetic refresh failure'); },
		async (value) => stored.push(value)
	);
	assert.equal(result, initial);
	assert.deepEqual(stored, [initial]);
	const auth = readFileSync(new URL('../src/routes/auth/+page.svelte', import.meta.url), 'utf8');
	assert.match(auth, /hydrateSignedInProfile\(sessionUser, getSessionUser, \(value\) => user\.set\(value\)\)/);
});

test('a hung refresh is aborted and a late response cannot replace the signed-in session', async () => {
	const initial = { id: 'member-4', name: 'Community member', token: 'valid-token' };
	const stored = [];
	let signal;
	let resolveRefresh;
	const pending = new Promise((resolve) => { resolveRefresh = resolve; });
	const result = await hydrateSignedInProfile(
		initial,
		async (_token, requestSignal) => {
			signal = requestSignal;
			return pending;
		},
		(value) => stored.push(value),
		5
	);
	assert.equal(result, initial);
	assert.equal(signal.aborted, true);
	assert.deepEqual(stored, [initial]);
	resolveRefresh({ bio: 'late profile value' });
	await new Promise((resolve) => setImmediate(resolve));
	assert.deepEqual(stored, [initial]);
	const auths = readFileSync(new URL('../src/lib/apis/auths/index.ts', import.meta.url), 'utf8');
	assert.match(auths, /getSessionUser = async \(token: string, signal\?: AbortSignal\)/);
	assert.match(auths, /\.\.\.\(signal \? \{ signal \} : \{\}\)/);
});

test('partial server capability omits unsupported values and keeps unrelated profile fields', () => {
	const enabled = exposedProfileFields({ bio: null, gender: 'Community' });
	const selected = supportedProfileValues(enabled, {
		bio: 'A short biography',
		gender: 'Community',
		date_of_birth: '1990-04-05'
	});
	const payload = buildUserProfileUpdatePayload('Updated name', 'updated-avatar', selected);
	assert.deepEqual(payload, {
		name: 'Updated name',
		profile_image_url: 'updated-avatar',
		bio: 'A short biography',
		gender: 'Community'
	});
	assert.equal(JSON.stringify(payload).includes('date_of_birth'), false);
});

test('unsupported session value types stay hidden instead of becoming accidental clears', () => {
	assert.deepEqual(exposedProfileFields({ bio: 4, gender: null, date_of_birth: '2024-02-31' }), {
		bio: false,
		gender: true,
		date_of_birth: false
	});
});

test('a rejected save leaves the editable draft and payload inputs untouched', async () => {
	const draft = { bio: 'Keep this draft', gender: 'Community', date_of_birth: '' };
	const before = { ...draft };
	const enabled = exposedProfileFields({ bio: null, gender: null, date_of_birth: null });
	const payload = buildUserProfileUpdatePayload(
		'Name',
		'avatar',
		supportedProfileValues(enabled, draft)
	);
	await assert.rejects(async () => {
		throw new Error('synthetic save failure');
	});
	assert.deepEqual(draft, before);
	assert.deepEqual(payload, {
		name: 'Name',
		profile_image_url: 'avatar',
		bio: 'Keep this draft',
		gender: 'Community',
		date_of_birth: null
	});
	const account = readFileSync(new URL('../src/lib/components/chat/Settings/Account.svelte', import.meta.url), 'utf8');
	const submit = account.slice(account.indexOf('const submitHandler'), account.indexOf('const createAPIKeyHandler'));
	assert.match(submit, /catch \(error\)[\s\S]*return false;/);
	assert.doesNotMatch(submit, /\b(?:bio|gender|dateOfBirth)\s*=/);
});

test('sign-in email and password retain explicit accessible label associations', async () => {
	const auth = readFileSync(new URL('../src/routes/auth/+page.svelte', import.meta.url), 'utf8');
	assert.match(auth, /<label\s+for="sign-in-email"[\s\S]*?<\/label>[\s\S]*?id="sign-in-email"/);
	assert.match(auth, /<label\s+for="sign-in-password"[\s\S]*?<\/label>[\s\S]*?id="sign-in-password"/);
});

test('optional account controls have explicit labels and matching IDs', async () => {
	const account = readFileSync(new URL('../src/lib/components/chat/Settings/Account.svelte', import.meta.url), 'utf8');
	for (const id of ['account-biography', 'account-gender', 'account-date-of-birth']) {
		assert.match(account, new RegExp(`<label[^>]+for="${id}"[\\s\\S]*?<\\/label>[\\s\\S]*?id="${id}"`));
	}
});

// SPDX-License-Identifier: BSD-3-Clause
const grantFields = new Set(['principal_type', 'principal_id', 'permission', 'id', 'resource_type', 'resource_id', 'created_at']);

export const parseResourceReadGrants = (model, resourceType, allowGroups = false) => {
	const legacyWrite = model?.access_control?.write;
	if (legacyWrite !== undefined && legacyWrite !== null &&
		(typeof legacyWrite !== 'object' || Array.isArray(legacyWrite) ||
		Object.keys(legacyWrite).some((key) => !['group_ids', 'user_ids'].includes(key)) ||
		Object.values(legacyWrite).some((ids) => !Array.isArray(ids) || ids.length > 0))) {
		return { supported: false, reason: 'Sharing uses permissions this editor cannot safely change.' };
	}
	let grants = model?.access_grants;
	if (!Array.isArray(grants)) {
		if (model?.access_control === null) grants = [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
		else if (model?.access_control && Object.keys(model.access_control).length === 0) grants = [];
		else return { supported: false, reason: 'Sharing uses permissions this editor cannot safely change.' };
	}
	const userIds = [];
	const groupIds = [];
	for (const grant of grants) {
		if (!grant || typeof grant !== 'object' || Array.isArray(grant) || Object.keys(grant).some((key) => !grantFields.has(key)) ||
			!['principal_type', 'principal_id', 'permission'].every((key) => key in grant) ||
			(grant.principal_type !== 'user' && !(allowGroups && grant.principal_type === 'group')) ||
			grant.permission !== 'read' || typeof grant.principal_id !== 'string' || !grant.principal_id.trim() ||
			(grant.principal_type === 'group' && grant.principal_id === '*') ||
			grant.principal_id.trim() !== grant.principal_id || grant.principal_id.length > 256 || /[\u0000-\u001f\u007f-\u009f]/u.test(grant.principal_id) ||
			(grant.resource_type != null && grant.resource_type !== resourceType) ||
			(grant.resource_id != null && grant.resource_id !== model?.id)) {
			return { supported: false, reason: 'Sharing uses permissions this editor cannot safely change.' };
		}
		(grant.principal_type === 'user' ? userIds : groupIds).push(grant.principal_id);
	}
	if (new Set(userIds).size !== userIds.length || new Set(groupIds).size !== groupIds.length ||
		(userIds.includes('*') && (userIds.length + groupIds.length !== 1))) {
		return { supported: false, reason: 'Sharing contains duplicate or conflicting permissions.' };
	}
	if (userIds.length === 1 && userIds[0] === '*') {
		return { supported: true, visibility: 'public', userIds: [], groupIds: [], accessControl: null };
	}
	return {
		supported: true,
		visibility: userIds.length && groupIds.length ? 'selected' : userIds.length ? 'users' : groupIds.length ? 'groups' : 'private',
		userIds,
		groupIds,
		accessControl: { read: { group_ids: groupIds, user_ids: [] }, write: { group_ids: [], user_ids: [] } }
	};
};

export const parseModelReadGrants = (model) => parseResourceReadGrants(model, 'model', true);

export const buildModelReadGrants = (visibility, userIdsText = '') => {
	if (visibility === 'private') return [];
	if (visibility === 'public') return [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
	if (visibility !== 'users' || typeof userIdsText !== 'string') throw new Error('Choose a supported model visibility.');
	const ids = userIdsText.split(/\r?\n/).map((id) => id.trim()).filter(Boolean);
	if (!ids.length || ids.some((id) => id === '*' || id.length > 256 || /[\u0000-\u001f\u007f-\u009f]/u.test(id)) ||
		new Set(ids).size !== ids.length) throw new Error('Enter distinct user IDs, one per line, for selected-user access.');
	return ids.map((principal_id) => ({ principal_type: 'user', principal_id, permission: 'read' }));
};

export const buildResourceReadGrants = buildModelReadGrants;

const validIds = (ids) => Array.isArray(ids) && ids.every((id) => typeof id === 'string' && id.trim() === id &&
	id.length > 0 && id.length <= 256 && !/[\u0000-\u001f\u007f-\u009f]/u.test(id)) && new Set(ids).size === ids.length;

export const buildModelAccessReadGrants = (accessControl, userIdsText = '') => {
	const userIds = typeof userIdsText === 'string'
		? userIdsText.split(/\r?\n/).map((id) => id.trim()).filter(Boolean)
		: null;
	if (!validIds(userIds ?? []) || userIds?.includes('*')) throw new Error('Enter distinct user IDs, one per line.');
	if (accessControl === null) {
		if (userIds.length) throw new Error('Public access cannot be combined with selected users.');
		return [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
	}
	const read = accessControl?.read;
	const write = accessControl?.write;
	const groupIds = read?.group_ids;
	const writeGroupIds = write?.group_ids ?? [];
	const writeUserIds = write?.user_ids ?? [];
	if (!validIds(groupIds) || groupIds.includes('*') || (read?.user_ids?.length ?? 0) > 0 ||
		!write || !validIds(writeGroupIds) || !validIds(writeUserIds) ||
		writeGroupIds.length > 0 || writeUserIds.length > 0) {
		throw new Error('This editor can change read access only.');
	}
	return [
		...groupIds.map((principal_id) => ({ principal_type: 'group', principal_id, permission: 'read' })),
		...userIds.map((principal_id) => ({ principal_type: 'user', principal_id, permission: 'read' }))
	];
};

export const buildReadGrantsFromParsed = (parsed) => parsed.visibility === 'public'
	? [{ principal_type: 'user', principal_id: '*', permission: 'read' }]
	: [
		...parsed.groupIds.map((principal_id) => ({ principal_type: 'group', principal_id, permission: 'read' })),
		...parsed.userIds.map((principal_id) => ({ principal_type: 'user', principal_id, permission: 'read' }))
	];

const grantKey = (grant) => `${grant.principal_type}\u0000${grant.principal_id}\u0000${grant.permission}`;
export const sameModelReadGrants = (left, right) =>
	Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
	JSON.stringify(left.map(grantKey).sort()) === JSON.stringify(right.map(grantKey).sort());

export const modelCreatePayload = (modelInfo) => {
	const { access_control, ...payload } = modelInfo;
	return payload;
};

export const saveResourceWithSharing = async (id, info, existingGrants, updateDetails, updateAccess) => {
	const { access_control, access_grants, ...details } = info;
	let saved;
	try { saved = await updateDetails(id, details); } catch { return { status: 'details_failed' }; }
	if (!saved) return { status: 'details_failed' };
	if (Array.isArray(access_grants) && !sameModelReadGrants(access_grants, existingGrants)) {
		try {
			const accessResult = await updateAccess(id, access_grants);
			if (!accessResult) return { status: 'sharing_failed', model: saved };
		} catch {
			return { status: 'sharing_failed', model: saved };
		}
	}
	return { status: 'saved', model: saved };
};

export const saveModelWithSharing = saveResourceWithSharing;

export const requestResourceAccessUpdate = async (fetchImpl, baseUrl, token, path, id, accessGrants) => {
	const response = await fetchImpl(`${baseUrl}${path}`, {
		method: 'POST',
		headers: { Accept: 'application/json', 'Content-Type': 'application/json', authorization: `Bearer ${token}` },
		body: JSON.stringify({ id, access_grants: accessGrants })
	});
	let result = null;
	try { result = await response.json(); } catch { /* the fixed error below avoids exposing response internals */ }
	if (!response.ok) throw new Error('Access grants could not be updated.');
	return result;
};

export const requestModelAccessUpdate = (fetchImpl, baseUrl, token, id, accessGrants) =>
	requestResourceAccessUpdate(fetchImpl, baseUrl, token, '/models/model/access/update', id, accessGrants);

export const requestToolAccessUpdate = (fetchImpl, baseUrl, token, id, accessGrants) =>
	requestResourceAccessUpdate(fetchImpl, baseUrl, token, `/tools/id/${encodeURIComponent(id)}/access/update`, id, accessGrants);

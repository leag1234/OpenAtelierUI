// SPDX-License-Identifier: BSD-3-Clause
const grantFields = new Set(['principal_type', 'principal_id', 'permission', 'id', 'resource_type', 'resource_id', 'created_at']);

export const parseModelReadGrants = (model) => {
	let grants = model?.access_grants;
	if (!Array.isArray(grants)) {
		if (model?.access_control === null) grants = [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
		else if (model?.access_control && Object.keys(model.access_control).length === 0) grants = [];
		else return { supported: false, reason: 'Sharing uses permissions this editor cannot safely change.' };
	}
	const userIds = [];
	for (const grant of grants) {
		if (!grant || typeof grant !== 'object' || Array.isArray(grant) || Object.keys(grant).some((key) => !grantFields.has(key)) ||
			!['principal_type', 'principal_id', 'permission'].every((key) => key in grant) || grant.principal_type !== 'user' ||
			grant.permission !== 'read' || typeof grant.principal_id !== 'string' || !grant.principal_id.trim() ||
			grant.principal_id.trim() !== grant.principal_id || grant.principal_id.length > 256 || /[\u0000-\u001f\u007f-\u009f]/u.test(grant.principal_id) ||
			(grant.resource_type && grant.resource_type !== 'model') ||
			(grant.resource_id && grant.resource_id !== model?.id)) {
			return { supported: false, reason: 'Sharing uses permissions this editor cannot safely change.' };
		}
		userIds.push(grant.principal_id);
	}
	if (new Set(userIds).size !== userIds.length || (userIds.includes('*') && userIds.length !== 1)) {
		return { supported: false, reason: 'Sharing contains duplicate or conflicting permissions.' };
	}
	if (userIds.length === 1 && userIds[0] === '*') return { supported: true, visibility: 'public', userIds: [] };
	return { supported: true, visibility: userIds.length ? 'users' : 'private', userIds };
};

export const buildModelReadGrants = (visibility, userIdsText = '') => {
	if (visibility === 'private') return [];
	if (visibility === 'public') return [{ principal_type: 'user', principal_id: '*', permission: 'read' }];
	if (visibility !== 'users' || typeof userIdsText !== 'string') throw new Error('Choose a supported model visibility.');
	const ids = userIdsText.split(/\r?\n/).map((id) => id.trim()).filter(Boolean);
	if (!ids.length || ids.some((id) => id === '*' || id.length > 256 || /[\u0000-\u001f\u007f-\u009f]/u.test(id)) ||
		new Set(ids).size !== ids.length) throw new Error('Enter distinct user IDs, one per line, for selected-user access.');
	return ids.map((principal_id) => ({ principal_type: 'user', principal_id, permission: 'read' }));
};

const grantKey = (grant) => `${grant.principal_type}\u0000${grant.principal_id}\u0000${grant.permission}`;
export const sameModelReadGrants = (left, right) =>
	Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
	JSON.stringify(left.map(grantKey).sort()) === JSON.stringify(right.map(grantKey).sort());

export const modelCreatePayload = (modelInfo) => {
	const { access_control, ...payload } = modelInfo;
	return payload;
};

export const saveModelWithSharing = async (id, modelInfo, existingGrants, updateDetails, updateAccess) => {
	const { access_control, access_grants, ...details } = modelInfo;
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

export const requestModelAccessUpdate = async (fetchImpl, baseUrl, token, id, accessGrants) => {
	const response = await fetchImpl(`${baseUrl}/models/model/access/update`, {
		method: 'POST',
		headers: { Accept: 'application/json', 'Content-Type': 'application/json', authorization: `Bearer ${token}` },
		body: JSON.stringify({ id, access_grants: accessGrants })
	});
	let result = null;
	try { result = await response.json(); } catch { /* the fixed error below avoids exposing response internals */ }
	if (!response.ok) throw new Error('Model sharing could not be updated.');
	return result;
};

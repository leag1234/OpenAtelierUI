/** @typedef {'bio' | 'gender' | 'date_of_birth'} ProfileField */
/** @typedef {'bio' | 'gender'} TextProfileField */
/** @typedef {{ bio?: string | null, gender?: string | null, date_of_birth?: string | null }} ProfileFields */
/** @typedef {{ bio: boolean, gender: boolean, date_of_birth: boolean }} ProfileFieldSupport */

/** @type {ProfileField[]} */
const PROFILE_FIELDS = ['bio', 'gender', 'date_of_birth'];

/** @param {Record<string, unknown> | null | undefined} sessionUser @param {TextProfileField} field */
const supportedNullableText = (sessionUser, field) =>
	Boolean(
		sessionUser &&
		Object.prototype.hasOwnProperty.call(sessionUser, field) &&
		(sessionUser[field] === null || typeof sessionUser[field] === 'string')
	);

/** @param {Record<string, unknown> | null | undefined} sessionUser */
const supportedNullableDate = (sessionUser) => {
	if (!sessionUser || !Object.prototype.hasOwnProperty.call(sessionUser, 'date_of_birth')) return false;
	const value = sessionUser.date_of_birth;
	if (value === null) return true;
	if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
	const parsed = new Date(`${value}T00:00:00Z`);
	return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
};

/** @param {Record<string, unknown> | null | undefined} sessionUser @returns {ProfileFieldSupport} */
export const exposedProfileFields = (sessionUser) => ({
	bio: supportedNullableText(sessionUser, 'bio'),
	gender: supportedNullableText(sessionUser, 'gender'),
	date_of_birth: supportedNullableDate(sessionUser)
});

/** @param {ProfileFieldSupport} enabled @param {ProfileFields} values @returns {ProfileFields} */
export const supportedProfileValues = (enabled, values) => ({
	...(enabled.bio ? { bio: values.bio } : {}),
	...(enabled.gender ? { gender: values.gender } : {}),
	...(enabled.date_of_birth ? { date_of_birth: values.date_of_birth } : {})
});

const SIGNED_IN_PROFILE_REFRESH_TIMEOUT_MS = 1500;

/** @param {Record<string, any> | null | undefined} sessionUser @param {(token: string, signal: AbortSignal) => Promise<any>} loadSessionUser @param {(value: any) => void | Promise<void>} setUser @param {number} [timeoutMs] */
export const hydrateSignedInProfile = async (
	sessionUser,
	loadSessionUser,
	setUser,
	timeoutMs = SIGNED_IN_PROFILE_REFRESH_TIMEOUT_MS
) => {
	await setUser(sessionUser);
	if (!sessionUser || typeof sessionUser.token !== 'string' || !sessionUser.token) return sessionUser;
	const controller = new AbortController();
	let timeout;
	let refreshed;
	try {
		refreshed = await Promise.race([
			loadSessionUser(sessionUser.token, controller.signal),
			new Promise((resolve) => {
				timeout = setTimeout(() => {
					controller.abort();
					resolve(null);
				}, timeoutMs);
			})
		]);
	} catch {
		return sessionUser;
	} finally {
		clearTimeout(timeout);
	}
	if (!refreshed || typeof refreshed !== 'object' || Array.isArray(refreshed)) return sessionUser;
	const fields = supportedProfileValues(exposedProfileFields(refreshed), refreshed);
	if (!Object.keys(fields).length) return sessionUser;
	const hydrated = { ...sessionUser, ...fields };
	await setUser(hydrated);
	return hydrated;
};

/** @param {string} name @param {string} profileImageUrl @param {ProfileFields} [fields] */
export const buildUserProfileUpdatePayload = (name, profileImageUrl, fields = {}) => {
	/** @type {Record<string, string | null>} */
	const payload = { name, profile_image_url: profileImageUrl };
	for (const field of PROFILE_FIELDS) {
		if (Object.prototype.hasOwnProperty.call(fields, field)) {
			const value = fields[field];
			if (value === undefined) continue;
			if (value !== null && typeof value !== 'string') {
				throw new TypeError('Profile field values must be strings or null');
			}
			payload[field] = value !== null && value !== '' ? value : null;
		}
	}
	return payload;
};

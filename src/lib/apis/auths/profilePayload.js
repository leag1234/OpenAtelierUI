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

/** @param {string} name @param {string} profileImageUrl @param {ProfileFields} [fields] */
export const buildUserProfileUpdatePayload = (name, profileImageUrl, fields = {}) => {
	/** @type {Record<string, string | null>} */
	const payload = { name, profile_image_url: profileImageUrl };
	for (const field of PROFILE_FIELDS) {
		if (Object.prototype.hasOwnProperty.call(fields, field)) {
			const value = fields[field];
			payload[field] = typeof value === 'string' && value !== '' ? value : null;
		}
	}
	return payload;
};

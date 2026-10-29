const MAX_STATUS_TEXT = 512;
const MAX_STATUS_QUERIES = 64;
const MAX_STATUS_URLS = 64;
const MAX_STATUS_URL_LENGTH = 2048;

const boundedText = (value, limit = MAX_STATUS_TEXT) => {
	if (typeof value !== 'string' || !value.trim() || value.length > limit) return false;
	for (let index = 0; index < value.length; index++) {
		const unit = value.charCodeAt(index);
		if (unit >= 0xd800 && unit <= 0xdbff) {
			const next = value.charCodeAt(++index);
			if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
		} else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
	}
	return true;
};

export const statusDescription = (status) => {
	if (status && typeof status === 'object' && boundedText(status.description)) return status.description;
	const action = status && typeof status === 'object' ? status.action : null;
	if (action === 'queries_generated') {
		const queries = status.queries;
		if (Array.isArray(queries) && queries.length <= MAX_STATUS_QUERIES && queries.every((query) => boundedText(query))) {
			if (queries.length === 0) return status.done === false ? 'Generating search queries' : 'No search queries generated';
			const preview = queries.slice(0, 3).map((query) => query.replace(/\s+/g, ' ').slice(0, 120)).join('; ');
			const remaining = queries.length > 3 ? `; and ${queries.length - 3} more` : '';
			return `Search queries: ${preview}${remaining}`;
		}
		return 'Search query update';
	}
	if (action === 'sources_retrieved') {
		const count = status.count;
		if (Number.isSafeInteger(count) && count >= 0 && count <= 10000) return `${count} ${count === 1 ? 'source' : 'sources'} retrieved${status.done === false ? ' so far' : ''}`;
		return 'Source retrieval update';
	}
	if (action === 'response_stopped') return 'Response stopped';
	return 'Progress update';
};

export const statusSearchQuery = (status) =>
	status && typeof status === 'object' && boundedText(status.query) ? status.query : null;

export const statusSearchUrls = (status) => {
	const urls = status && typeof status === 'object' ? status.urls : null;
	return Array.isArray(urls) && urls.length <= MAX_STATUS_URLS &&
		urls.every((url) => {
			if (!boundedText(url, MAX_STATUS_URL_LENGTH)) return false;
			try {
				const parsed = new URL(url);
				return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password;
			} catch { return false; }
		}) ? urls : null;
};

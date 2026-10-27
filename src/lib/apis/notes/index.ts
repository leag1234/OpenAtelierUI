import { WEBUI_API_BASE_URL } from '$lib/constants';

const request = async (token: string, path: string, method = 'GET', body?: object) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/notes${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	if (!response.ok) {
		const error = new Error('Private notes request failed');
		(error as Error & { status?: number }).status = response.status;
		throw error;
	}
	return response.json();
};

export const listNotes = (token: string, page = 1) => request(token, `/?page=${page}`);
export const searchNotes = (token: string, query: string, page = 1) =>
	request(token, `/search?query=${encodeURIComponent(query)}&page=${page}`);
export const getNote = (token: string, id: string) => request(token, `/${encodeURIComponent(id)}`);
export const createNote = (token: string, note: object) => request(token, '/create', 'POST', note);
export const updateNote = (token: string, id: string, note: object) =>
	request(token, `/${encodeURIComponent(id)}/update`, 'POST', note);
export const toggleNotePin = (token: string, id: string) =>
	request(token, `/${encodeURIComponent(id)}/pin`, 'POST', {});

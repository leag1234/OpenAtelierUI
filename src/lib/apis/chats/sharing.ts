// SPDX-License-Identifier: BSD-3-Clause
import { WEBUI_API_BASE_URL } from '$lib/constants';

async function request(token: string, path: string, body?: object) {
 const response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
  method: body === undefined ? 'GET' : 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  ...(body === undefined ? {} : { body: JSON.stringify(body) })
 });
 const value = await response.json();
 if (!response.ok) throw new Error(typeof value?.detail === 'string' ? value.detail : 'Sharing could not be updated.');
 return value;
}
export const getChatGrants = (token: string, id: string) => request(token, `/chats/shared/${encodeURIComponent(id)}/access`);
export const setChatGrants = (token: string, id: string, grants: object[]) => request(token, `/chats/shared/${encodeURIComponent(id)}/access/update`, { access_grants: grants });
export const searchRecipients = (token: string, query: string) => request(token, `/users/search?${new URLSearchParams({ query, order_by: 'name', direction: 'asc', page: '1' })}`);

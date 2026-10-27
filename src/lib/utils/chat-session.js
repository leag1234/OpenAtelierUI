// SPDX-License-Identifier: BSD-3-Clause

export function selectChatEventChannel(config) {
	return config && config.event_call_channel === 'events' ? 'events' : 'chat-events';
}

export function bindChatEventChannel(socket, handler, config) {
	const channel = selectChatEventChannel(config);
	if (!socket) {
		return { socket: null, channel, cleanup: () => {} };
	}

	socket.on(channel, handler);
	return {
		socket,
		channel,
		cleanup: () => socket.off(channel, handler)
	};
}

export function normalizeTaskIds(response) {
	const ids = response && Array.isArray(response.task_ids) ? response.task_ids : [];
	const normalized = ids.filter((id) => typeof id === 'string' && id.length > 0);
	if (response && typeof response.task_id === 'string' && response.task_id.length > 0) {
		normalized.push(response.task_id);
	}
	return [...new Set(normalized)];
}

export function mergeTaskIds(currentIds, response) {
	return [...new Set([...normalizeTaskIds({ task_ids: currentIds }), ...normalizeTaskIds(response)])];
}

export function chatResultMatchesCurrent(requestChatId, currentChatId) {
	return typeof requestChatId === 'string' && requestChatId === currentChatId;
}

export function isEphemeralChatId(chatId) {
	return chatId === 'local' || (typeof chatId === 'string' && chatId.startsWith('local:'));
}

export function chatTaskStopPath(chatId) {
	return `/api/tasks/chat/${encodeURIComponent(chatId)}/stop`;
}

export async function requestSavedChatStop(stopRequest, listRequest, isStillCurrent) {
	let stopResponse;
	try {
		stopResponse = await stopRequest();
	} catch (error) {
		return { state: isStillCurrent() ? 'error' : 'navigated', error };
	}
	if (!isStillCurrent()) return { state: 'navigated' };

	let taskListResponse = null;
	if (stopResponse && stopResponse.status === false) {
		try {
			taskListResponse = await listRequest();
		} catch (error) {
			return { state: isStillCurrent() ? 'error' : 'navigated', error };
		}
		if (!isStillCurrent()) return { state: 'navigated' };
	}

	const stopped = stopResponse && stopResponse.status === true;
	const alreadyFinished = stopResponse && stopResponse.status === false &&
		taskListResponse && Array.isArray(taskListResponse.task_ids) &&
		taskListResponse.task_ids.length === 0;
	return {
		state: stopped ? 'stopped' : alreadyFinished ? 'already-finished' : 'uncertain',
		stopResponse,
		taskListResponse
	};
}

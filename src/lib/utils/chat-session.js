// SPDX-License-Identifier: BSD-3-Clause
// Copyright (c) 2026 Gaël Duval

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

export function registerConnectedSocketUser(socket, token) {
	if (
		!socket ||
		typeof socket.emit !== 'function' ||
		typeof token !== 'string' ||
		token.length === 0
	) {
		return false;
	}

	socket.emit('user-join', { auth: { token } });
	return true;
}

export function waitForConnectedSocket(socket, timeoutMs = 15000) {
	if (!socket) return Promise.reject(new Error('socket_unavailable'));
	if (socket.connected && typeof socket.id === 'string' && socket.id.length > 0) {
		return Promise.resolve(socket);
	}

	return new Promise((resolve, reject) => {
		let settled = false;
		const finish = (error) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			socket.off('connect', onConnect);
			socket.off('connect_error', onConnectError);
			if (error) reject(error);
			else resolve(socket);
		};
		const onConnect = () => {
			if (typeof socket.id === 'string' && socket.id.length > 0) finish();
		};
		const onConnectError = () => {
			// Socket.IO owns reconnection. Keep the bounded waiter active until a
			// later successful connect or the deadline.
		};
		const timer = setTimeout(() => finish(new Error('socket_reconnect_timeout')), timeoutMs);
		socket.on('connect', onConnect);
		socket.on('connect_error', onConnectError);
		if (typeof socket.connect === 'function' && socket.disconnected) socket.connect();
	});
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

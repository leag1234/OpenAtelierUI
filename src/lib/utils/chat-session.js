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

export function confirmConnectedSocketUser(socket, token, timeoutMs = 5000) {
	if (
		!socket ||
		typeof socket.emit !== 'function' ||
		typeof token !== 'string' ||
		token.length === 0
	) {
		return Promise.reject(new Error('socket_registration_unavailable'));
	}

	return new Promise((resolve, reject) => {
		let settled = false;
		const finish = (error, acknowledgement) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			if (error) reject(error);
			else resolve(acknowledgement);
		};
		const timer = setTimeout(
			() => finish(new Error('socket_registration_timeout')),
			timeoutMs
		);
		socket.emit('user-join', { auth: { token } }, (acknowledgement) => {
			if (
				acknowledgement &&
				typeof acknowledgement.id === 'string' &&
				acknowledgement.id.length > 0
			) {
				finish(null, acknowledgement);
			} else {
				finish(new Error('socket_registration_rejected'));
			}
		});
	});
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

export function shouldRequestInitialChatTasks({
	temporary,
	title,
	defaultTitle = 'New Chat',
	messageCount,
	selectedModelMatches
}) {
	return (
		!temporary &&
		(typeof title !== 'string' || title.length === 0 || title === defaultTitle) &&
		(messageCount === 1 || messageCount === 2) &&
		selectedModelMatches === true
	);
}

export function isEphemeralChatId(chatId) {
	return chatId === 'local' || (
		typeof chatId === 'string' &&
		(chatId.startsWith('local:') || chatId.startsWith('temporary:'))
	);
}

export function isTemporaryChatId(chatId) {
	return typeof chatId === 'string' && chatId.startsWith('temporary:');
}

export function createTemporaryChatId(sessionId, randomId) {
	if (typeof sessionId !== 'string' || sessionId.length === 0) return null;
	if (typeof randomId !== 'string' || randomId.length === 0) return null;
	return `temporary:${sessionId}:${randomId}`;
}

export function chatTaskStopPath(chatId) {
	return `/api/tasks/chat/${encodeURIComponent(chatId)}/stop`;
}

export function chatTaskResumePath(chatId) {
	return `/api/tasks/chat/${encodeURIComponent(chatId)}/resume`;
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

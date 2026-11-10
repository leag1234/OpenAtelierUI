<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { callMCPAppRPC } from '$lib/apis/tools';

	export let app: {
		server_id: string;
		tool_name: string;
		binding: string;
		resource_uri: string;
	};
	export let toolInput: Record<string, unknown> = {};
	export let toolResult: Record<string, unknown> = {};

	let html = '';
	let error = '';
	let loading = true;
	let activeRequest: AbortController | null = null;
	let requestGeneration = 0;
	let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
	let iframeElement: HTMLIFrameElement | null = null;
	let frameHeight = 256;
	let initialized = false;
	let hostRequestId = 1;
	const appResourceTimeoutMs = 8_000;
	const minFrameHeight = 160;
	const maxFrameHeight = 800;
	const mcpAppsProtocolVersion = '2026-01-26';
	const appCSP =
		"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

	const applyCSP = (source: string) => {
		const meta = `<meta http-equiv="Content-Security-Policy" content="${appCSP}">`;
		if (/<head[\s>]/i.test(source)) {
			return source.replace(/<head([^>]*)>/i, `<head$1>${meta}`);
		}
		return `${meta}${source}`;
	};

	const postToView = (message: Record<string, unknown>) => {
		iframeElement?.contentWindow?.postMessage(message, '*');
	};

	const sanitizedToolResult = () => {
		const result = { ...toolResult };
		if (result._meta && typeof result._meta === 'object') {
			const metadata = { ...(result._meta as Record<string, unknown>) };
			delete metadata.openatelier_mcp_app;
			result._meta = metadata;
		}
		return result;
	};

	const respond = (id: unknown, result?: unknown, error?: { code: number; message: string }) => {
		if (typeof id !== 'string' && typeof id !== 'number') {
			return;
		}
		postToView(error ? { jsonrpc: '2.0', id, error } : { jsonrpc: '2.0', id, result });
	};

	const sendToolData = () => {
		postToView({
			jsonrpc: '2.0',
			method: 'ui/notifications/tool-input',
			params: { arguments: toolInput && typeof toolInput === 'object' ? toolInput : {} }
		});
		postToView({
			jsonrpc: '2.0',
			method: 'ui/notifications/tool-result',
			params: sanitizedToolResult()
		});
	};

	const handleViewMessage = (event: MessageEvent) => {
		if (!iframeElement?.contentWindow || event.source !== iframeElement.contentWindow) {
			return;
		}
		const message = event.data;
		if (!message || typeof message !== 'object' || message.jsonrpc !== '2.0') {
			return;
		}
		if (message.method === 'ui/initialize') {
			const rect = iframeElement.getBoundingClientRect();
			respond(message.id, {
				protocolVersion: mcpAppsProtocolVersion,
				hostCapabilities: {
					sandbox: { permissions: {}, csp: { connectDomains: [], resourceDomains: [] } }
				},
				hostInfo: { name: 'OpenAtelierUI', version: '0.6.5' },
				hostContext: {
					theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
					displayMode: 'inline',
					availableDisplayModes: ['inline'],
					containerDimensions: { maxWidth: Math.round(rect.width), maxHeight: maxFrameHeight },
					locale: navigator.language,
					timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
					userAgent: navigator.userAgent,
					platform: window.matchMedia('(max-width: 640px)').matches ? 'mobile' : 'web',
					deviceCapabilities: {
						touch: navigator.maxTouchPoints > 0,
						hover: window.matchMedia('(hover: hover)').matches
					}
				}
			});
			return;
		}
		if (message.method === 'ui/notifications/initialized') {
			if (!initialized) {
				initialized = true;
				sendToolData();
			}
			return;
		}
		if (message.method === 'ui/notifications/size-changed') {
			const requested = Number(message.params?.height);
			if (Number.isFinite(requested)) {
				frameHeight = Math.min(maxFrameHeight, Math.max(minFrameHeight, Math.round(requested)));
			}
			return;
		}
		if (message.method === 'ping') {
			respond(message.id, {});
			return;
		}
		if ('id' in message) {
			respond(message.id, undefined, {
				code: -32601,
				message: 'This host action is not available in the inline application.'
			});
		}
	};

	const clearRequestTimeout = () => {
		if (timeoutHandle !== null) {
			clearTimeout(timeoutHandle);
			timeoutHandle = null;
		}
	};

	const stopRequest = (message: string) => {
		if (!activeRequest) {
			return;
		}
		requestGeneration += 1;
		activeRequest.abort();
		activeRequest = null;
		clearRequestTimeout();
		html = '';
		error = message;
		loading = false;
	};

	const cancelLoad = () => {
		stopRequest('Interactive tool result loading was cancelled.');
	};

	const load = async () => {
		activeRequest?.abort();
		clearRequestTimeout();
		const generation = ++requestGeneration;
		const controller = new AbortController();
		activeRequest = controller;
		loading = true;
		error = '';
		html = '';
		timeoutHandle = setTimeout(() => {
			if (generation === requestGeneration) {
				stopRequest('The interactive tool result timed out.');
			}
		}, appResourceTimeoutMs);
		try {
			const response = await callMCPAppRPC(localStorage.token, app.server_id, {
				tool_name: app.tool_name,
				binding: app.binding,
				method: 'resources/read',
				params: { uri: app.resource_uri }
			}, controller.signal);
			if (generation !== requestGeneration) {
				return;
			}
			const content = response?.contents?.find(
				(item: { uri?: string; mimeType?: string; text?: unknown }) =>
					item?.uri === app.resource_uri && item?.mimeType === 'text/html;profile=mcp-app'
			);
			if (!content || typeof content.text !== 'string') {
				throw new Error('The interactive tool did not return a supported application resource.');
			}
			html = applyCSP(content.text);
		} catch (exception) {
			if (generation !== requestGeneration) {
				return;
			}
			error = exception instanceof Error ? exception.message : String(exception);
			html = '';
		} finally {
			if (generation === requestGeneration) {
				clearRequestTimeout();
				activeRequest = null;
				loading = false;
			}
		}
	};

	onMount(() => {
		window.addEventListener('message', handleViewMessage);
		load();
	});
	onDestroy(() => {
		if (initialized) {
			postToView({
				jsonrpc: '2.0',
				id: `host-${hostRequestId++}`,
				method: 'ui/resource-teardown',
				params: { reason: 'The inline application was removed.' }
			});
		}
		window.removeEventListener('message', handleViewMessage);
		requestGeneration += 1;
		activeRequest?.abort();
		activeRequest = null;
		clearRequestTimeout();
	});
</script>

<div class="my-2 w-full overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
	{#if loading}
		<div class="flex items-center justify-between gap-3 p-4">
			<span class="text-sm text-gray-500" role="status">Loading interactive tool result…</span>
			<button
				type="button"
				class="shrink-0 whitespace-nowrap rounded-lg border px-3 py-1.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
				on:click={cancelLoad}>Cancel</button
			>
		</div>
	{:else if error}
		<div class="flex items-center justify-between gap-3 p-4" role="alert">
			<span class="text-sm text-red-600 dark:text-red-400">{error}</span>
			<button
				type="button"
				class="shrink-0 whitespace-nowrap rounded-lg border px-3 py-1.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
				on:click={load}>Retry</button
			>
		</div>
	{:else}
		<iframe
			bind:this={iframeElement}
			title="Interactive tool result"
			sandbox="allow-scripts"
			srcdoc={html}
			style:height={`${frameHeight}px`}
			class="block w-full border-0 bg-white"
		></iframe>
	{/if}
</div>

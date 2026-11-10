<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { callMCPAppRPC } from '$lib/apis/tools';

	export let app: {
		server_id: string;
		tool_name: string;
		binding: string;
		resource_uri: string;
	};

	let html = '';
	let error = '';
	let loading = true;
	let activeRequest: AbortController | null = null;
	let requestGeneration = 0;
	let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
	const appResourceTimeoutMs = 20_000;
	const appCSP =
		"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

	const applyCSP = (source: string) => {
		const meta = `<meta http-equiv="Content-Security-Policy" content="${appCSP}">`;
		if (/<head[\s>]/i.test(source)) {
			return source.replace(/<head([^>]*)>/i, `<head$1>${meta}`);
		}
		return `${meta}${source}`;
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

	onMount(load);
	onDestroy(() => {
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
			title="Interactive tool result"
			sandbox="allow-scripts"
			srcdoc={html}
			class="block h-64 w-full border-0 bg-white"
		></iframe>
	{/if}
</div>

<script lang="ts">
	import { onMount } from 'svelte';
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
	const appCSP =
		"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

	const applyCSP = (source: string) => {
		const meta = `<meta http-equiv="Content-Security-Policy" content="${appCSP}">`;
		if (/<head[\s>]/i.test(source)) {
			return source.replace(/<head([^>]*)>/i, `<head$1>${meta}`);
		}
		return `${meta}${source}`;
	};

	const load = async () => {
		loading = true;
		error = '';
		try {
			const response = await callMCPAppRPC(localStorage.token, app.server_id, {
				tool_name: app.tool_name,
				binding: app.binding,
				method: 'resources/read',
				params: { uri: app.resource_uri }
			});
			const content = response?.contents?.find(
				(item: { uri?: string; mimeType?: string; text?: unknown }) =>
					item?.uri === app.resource_uri && item?.mimeType === 'text/html;profile=mcp-app'
			);
			if (!content || typeof content.text !== 'string') {
				throw new Error('The interactive tool did not return a supported application resource.');
			}
			html = applyCSP(content.text);
		} catch (exception) {
			error = exception instanceof Error ? exception.message : String(exception);
			html = '';
		} finally {
			loading = false;
		}
	};

	onMount(load);
</script>

<div class="my-2 w-full overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
	{#if loading}
		<div class="p-4 text-sm text-gray-500" role="status">Loading interactive tool result…</div>
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

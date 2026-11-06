<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import { getFiles } from '$lib/apis/files';
	import { getKnowledgeBases } from '$lib/apis/knowledge';
	import { getChatList, getChatById } from '$lib/apis/chats';
	import { createMessagesList } from '$lib/utils';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();
	export let mode = '';
	export let onSelect = async (_item) => {};
	let show = false;
	let activeMode = '';
	let items = [];
	let query = '';
	let url = '';
	let loading = false;
	let error = '';
	let busy = false;
	let generation = 0;
	$: if (mode && mode !== activeMode) load(mode);
	$: if (!show && activeMode) { mode = ''; activeMode = ''; generation += 1; }
	$: visible = items.filter((item) => String(item.name || item.filename || item.title || '').toLocaleLowerCase().includes(query.toLocaleLowerCase()));
	$: title = { web: 'Attach web page', files: 'Attach files', knowledge: 'Attach knowledge', chats: 'Reference chats' }[mode] || 'Attach files';

	async function load(nextMode) {
		activeMode = nextMode;
		show = true;
		items = []; query = ''; url = ''; error = ''; loading = true;
		const epoch = ++generation;
		try {
			const result = nextMode === 'files' ? await getFiles(localStorage.token)
				: nextMode === 'knowledge' ? await getKnowledgeBases(localStorage.token)
				: nextMode === 'chats' ? await getChatList(localStorage.token) : [];
			if (generation === epoch) items = Array.isArray(result) ? result : result?.items || result?.data || [];
		} catch (failure) { if (generation === epoch) error = String(failure); }
		finally { if (generation === epoch) loading = false; }
	}

	async function select(item) {
		if (busy) return;
		busy = true; error = '';
		try {
			if (mode === 'chats') {
				const chat = await getChatById(localStorage.token, item.id);
				const body = chat.chat || chat;
				const messages = body.history ? createMessagesList(body.history, body.history.currentId) : body.messages || [];
				const text = messages.map((message) => `${message.role}: ${typeof message.content === 'string' ? message.content : JSON.stringify(message.content)}`).join('\n\n');
				// Attach an explicit snapshot through the normal authorized upload path.
				await onSelect({ snapshot: new File([text], `${item.title || 'Conversation'}.txt`, { type: 'text/plain' }) });
			} else {
				await onSelect({ ...item, type: mode === 'knowledge' ? 'collection' : 'file', name: item.name || item.filename || item.meta?.name });
			}
			show = false;
		} catch (failure) { error = String(failure); }
		finally { busy = false; }
	}

	function attachWeb() {
		try {
			const parsed = new URL(url);
			if (!['https:', 'http:'].includes(parsed.protocol)) throw new Error($i18n.t('Enter a valid URL'));
			dispatch('web', parsed.href); show = false;
		} catch { error = $i18n.t('Enter a valid URL'); }
	}
</script>

<Modal bind:show size="sm">
	<div class="p-5 text-gray-900 dark:text-gray-100">
		<div class="mb-4 flex items-center justify-between gap-3">
			<h2 class="text-lg font-medium">{$i18n.t(title)}</h2>
			<button type="button" aria-label={$i18n.t('Close')} on:click={() => show = false} class="rounded-lg p-2">✕</button>
		</div>
		{#if mode === 'web'}
			<form on:submit|preventDefault={attachWeb} class="flex gap-2">
				<input type="url" required bind:value={url} aria-label={$i18n.t('Web address')} placeholder="https://" class="min-w-0 flex-1 rounded-lg border p-2 dark:bg-gray-850" />
				<button type="submit" class="rounded-lg bg-gray-100 px-3 dark:bg-gray-800">{$i18n.t('Attach')}</button>
			</form>
		{:else}
			<input bind:value={query} aria-label={$i18n.t('Search')} placeholder={$i18n.t('Search')} class="mb-3 w-full rounded-lg border p-2 dark:bg-gray-850" />
			<div class="max-h-80 overflow-auto" aria-busy={loading || busy}>
				{#if loading}<p>{$i18n.t('Loading...')}</p>
				{:else if !visible.length}<p>{$i18n.t('No results found')}</p>
				{:else}{#each visible as item (item.id)}
					<button type="button" disabled={busy} on:click={() => select(item)} class="block w-full truncate rounded-lg p-3 text-left hover:bg-gray-100 dark:hover:bg-gray-800">{item.name || item.filename || item.title || item.meta?.name}</button>
				{/each}{/if}
			</div>
		{/if}
		{#if error}<p role="alert" class="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>{/if}
	</div>
</Modal>

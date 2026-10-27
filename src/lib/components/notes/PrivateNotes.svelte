<script>
	import { onMount, onDestroy } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import { config } from '$lib/stores';
	import { listNotes, searchNotes, getNote, createNote, updateNote, toggleNotePin } from '$lib/apis/notes';
	import { editableNote, isDirty, notePayload, resolveSave, safeNoteFilename } from '$lib/apis/notes/state.js';

	let notes = [];
	let total = null;
	let page = 1;
	let query = '';
	let appliedQuery = '';
	let selected = null;
	let newNoteMode = false;
	let baseline = { title: '', text: '' };
	let title = '';
	let text = '';
	let editable = true;
	let loading = true;
	let saving = false;
	let status = '';
	let pendingAction = null;
	let selectionVersion = 0;
	let listVersion = 0;
	let accountRefused = false;
	$: dirty = isDirty({ title, text }, baseline);

	const token = () => localStorage.token;
	const setFailure = (error) => {
		accountRefused = error?.status === 403 || error?.status === 401;
		status = accountRefused
			? 'This account cannot access private notes.'
			: error?.status === 422
				? 'This note or search exceeds the supported format or size.'
				: 'Private notes are unavailable right now.';
	};

	const refreshList = async (targetPage = page, targetQuery = appliedQuery) => {
		const version = ++listVersion;
		loading = true;
		try {
			const result = targetQuery
				? await searchNotes(token(), targetQuery, targetPage)
				: { items: await listNotes(token(), targetPage), total: null };
			if (version !== listVersion) return false;
			notes = result.items ?? [];
			total = result.total ?? null;
			page = targetPage;
			appliedQuery = targetQuery;
			status = '';
			accountRefused = false;
			return true;
		} catch (error) {
			if (version === listVersion) setFailure(error);
			return false;
		} finally {
			if (version === listVersion) loading = false;
		}
	};

	const openNote = async (id) => {
		const version = ++selectionVersion;
		try {
			status = 'Loading note…';
			const note = await getNote(token(), id);
			if (version !== selectionVersion) return;
			const parsed = editableNote(note);
			selected = note;
			newNoteMode = false;
			editable = parsed.editable;
			title = note.title ?? '';
			text = parsed.text;
			baseline = { title, text };
			status = '';
		} catch (error) {
			if (version === selectionVersion) setFailure(error);
		}
	};

	const startNew = () => {
		selectionVersion++;
		selected = null;
		newNoteMode = true;
		title = '';
		text = '';
		baseline = { title: '', text: '' };
		editable = true;
		status = '';
	};

	const requestAction = (action) => {
		if (saving) return;
		if (dirty) pendingAction = action;
		else action();
	};

	const discardAndContinue = () => {
		const action = pendingAction;
		pendingAction = null;
		if (action) action();
	};

	const save = async () => {
		if (!editable || saving || !title.trim()) return false;
		saving = true;
		status = 'Saving…';
		const version = selectionVersion;
		const draft = { selected, title, text, baseline, editable };
		try {
			const payload = notePayload(title, text, selected);
			const saved = selected
				? await updateNote(token(), selected.id, payload)
				: await createNote(token(), payload);
			if (version !== selectionVersion) return false;
			const result = resolveSave(draft, saved);
			selected = result.selected;
			baseline = result.baseline;
			await refreshList(1, appliedQuery);
			status = result.status === 'saved' ? 'Saved' : result.status;
			return true;
		} catch (error) {
			const retained = resolveSave(draft, null, true);
			selected = retained.selected;
			title = retained.title;
			text = retained.text;
			baseline = retained.baseline;
			editable = retained.editable;
			setFailure(error);
			return false;
		} finally {
			saving = false;
		}
	};

	const saveAndContinue = async () => {
		if (await save()) discardAndContinue();
	};

	const applySearch = async (event) => {
		event.preventDefault();
		await refreshList(1, query.trim());
	};

	const pin = async () => {
		if (!selected || saving) return;
		const id = selected.id;
		try {
			const updated = await toggleNotePin(token(), id);
			if (selected?.id === id) selected = { ...selected, is_pinned: updated.is_pinned };
			notes = notes.map((note) => note.id === id ? { ...note, is_pinned: updated.is_pinned } : note);
		} catch (error) { setFailure(error); }
	};

	const downloadOriginal = () => {
		if (!selected) return;
		const blob = new Blob([JSON.stringify(selected, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const anchor = document.createElement('a');
		anchor.href = url;
		anchor.download = safeNoteFilename();
		anchor.click();
		URL.revokeObjectURL(url);
	};

	const beforeUnload = (event) => {
		if (!dirty) return;
		event.preventDefault();
		event.returnValue = '';
	};
	const onKeyDown = (event) => {
		if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && editable) {
			event.preventDefault();
			save();
		}
	};

	beforeNavigate(({ cancel }) => {
		if (dirty && !window.confirm('You have unsaved note changes. Leave this page and discard them?')) cancel();
	});

	onMount(() => {
		window.addEventListener('beforeunload', beforeUnload);
		window.addEventListener('keydown', onKeyDown);
		if ($config?.features?.openatelier_private_notes) refreshList(1, '');
	});
		onDestroy(() => {
			window.removeEventListener('beforeunload', beforeUnload);
			window.removeEventListener('keydown', onKeyDown);
		});
</script>

<svelte:head><title>Private Notes · {$config?.name ?? 'OpenAtelierUI'}</title></svelte:head>

<main class="min-h-screen bg-white text-gray-900 dark:bg-gray-950 dark:text-gray-100">
	<div class="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
		<header class="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-gray-200 pb-4 dark:border-gray-800">
			<div><p class="text-xs font-semibold uppercase tracking-wide text-gray-500">Private workspace</p><h1 class="mt-1 text-2xl font-semibold">Notes</h1><p class="mt-1 text-sm text-gray-600 dark:text-gray-300">Only you can read these notes.</p></div>
			{#if $config?.features?.openatelier_private_notes}<button class="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900" on:click={() => requestAction(startNew)} disabled={saving}>New note</button>{/if}
		</header>
		{#if !$config?.features?.openatelier_private_notes}
			<section class="rounded-xl border border-gray-200 p-6 dark:border-gray-800" role="status"><h2 class="font-semibold">Private notes are unavailable</h2><p class="mt-2 text-sm text-gray-600 dark:text-gray-300">This account cannot open the private notes workspace. Your chats remain available.</p><a class="mt-4 inline-flex rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900" href="/">Return to chat</a></section>
		{:else}
			{#if accountRefused}<p class="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100" role="status">This account cannot access private notes. <a class="underline" href="/">Return to chat</a></p>{/if}
			{#if status}<p class="mb-3 rounded-lg bg-gray-100 px-3 py-2 text-sm dark:bg-gray-900" role="status">{status}</p>{/if}
			{#if loading}<p class="mb-3 text-sm text-gray-500" role="status">Loading notes…</p>{/if}
			<div class="grid min-h-[65vh] grid-cols-1 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800 lg:grid-cols-[18rem_minmax(0,1fr)]">
				<aside class="border-b border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-900/40 lg:border-b-0 lg:border-r">
					<form class="flex gap-2" on:submit={applySearch}><label class="sr-only" for="note-search">Search note titles</label><input id="note-search" bind:value={query} maxlength="256" class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950" placeholder="Search titles"/><button class="rounded-lg border border-gray-300 px-3 text-sm hover:bg-white dark:border-gray-700 dark:hover:bg-gray-800">Search</button></form>
					<div class="mt-3 max-h-80 space-y-1 overflow-y-auto lg:max-h-[55vh]" aria-label="Your notes">
						{#each notes as note (note.id)}<button class="w-full rounded-lg px-3 py-2 text-left hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:hover:bg-gray-800 {selected?.id === note.id ? 'bg-white shadow-sm dark:bg-gray-800' : ''}" on:click={() => requestAction(() => openNote(note.id))}><span class="block truncate text-sm font-medium">{note.title || 'Untitled note'}</span><span class="mt-1 flex items-center gap-2 text-xs text-gray-500">{#if note.is_pinned}<span>Pinned</span>{/if}<span>{new Date(note.updated_at * 1000).toLocaleDateString()}</span></span></button>{/each}
						{#if !loading && notes.length === 0}<p class="px-3 py-4 text-sm text-gray-500">{appliedQuery ? 'No matching titles.' : 'No saved notes yet.'}</p>{/if}
					</div>
					<div class="mt-3 flex items-center justify-between border-t border-gray-200 pt-3 text-xs text-gray-500 dark:border-gray-800"><span>{total === null ? `Page ${page}` : `${total} ${total === 1 ? 'note' : 'notes'}`}</span><div class="flex gap-2"><button class="rounded px-2 py-1 hover:bg-white disabled:opacity-40 dark:hover:bg-gray-800" on:click={() => refreshList(Math.max(1, page - 1), appliedQuery)} disabled={page <= 1 || loading}>Previous</button><button class="rounded px-2 py-1 hover:bg-white disabled:opacity-40 dark:hover:bg-gray-800" on:click={() => refreshList(page + 1, appliedQuery)} disabled={loading || notes.length < 32}>Next</button></div></div>
				</aside>
				<section class="flex min-w-0 flex-col p-4 sm:p-6" aria-label="Note editor">
					{#if selected && !editable}
						<div class="flex flex-wrap items-center justify-between gap-3"><div><p class="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">Read-only note data</p><h2 class="mt-1 text-xl font-semibold">{selected.title}</h2></div><button class="rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900" on:click={downloadOriginal}>Download note JSON</button></div>
						<p class="mt-3 text-sm text-gray-600 dark:text-gray-300">This note contains fields the plain-text editor cannot safely preserve. It has not been changed.</p><pre class="mt-4 max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-4 text-xs dark:bg-gray-900">{JSON.stringify(selected, null, 2)}</pre>
					{:else if selected || newNoteMode}
						<div class="flex flex-wrap items-center justify-between gap-3"><div><p class="text-xs font-semibold uppercase tracking-wide text-gray-500">Private note</p><h2 class="mt-1 text-lg font-semibold">{selected ? 'Edit note' : 'New note'}</h2></div>{#if selected}<button class="rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900" on:click={pin} disabled={saving}>{selected.is_pinned ? 'Unpin' : 'Pin'}</button>{/if}</div>
						<label class="mt-5 text-sm font-medium" for="note-title">Title</label><input id="note-title" bind:value={title} disabled={saving} class="mt-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-base dark:border-gray-700 dark:bg-gray-950" placeholder="A title for this note"/>
						<label class="mt-4 text-sm font-medium" for="note-text">Note</label><textarea id="note-text" bind:value={text} disabled={saving} class="mt-1 min-h-64 flex-1 resize-y rounded-lg border border-gray-300 bg-white px-3 py-3 text-sm leading-6 dark:border-gray-700 dark:bg-gray-950" placeholder="Write a plan, reminder, or idea…" spellcheck="true"></textarea>
						<div class="mt-4 flex flex-wrap items-center justify-between gap-3"><span class="text-sm text-gray-500" aria-live="polite">{dirty ? 'Unsaved changes' : status || 'Saved'}</span><button class="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900" on:click={save} disabled={!dirty || saving || !title.trim()}>{saving ? 'Saving…' : 'Save note'}</button></div>
					{:else}<div class="m-auto max-w-sm text-center"><h2 class="text-lg font-semibold">Your notes, in one private place</h2><p class="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">Save a workshop plan or reminder, then find it again when you need it.</p><button class="mt-4 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white dark:bg-gray-100 dark:text-gray-900" on:click={startNew}>Create a note</button></div>{/if}
				</section>
			</div>
		{/if}
	</div>
</main>

{#if pendingAction}
	<div class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" role="presentation"><section class="w-full max-w-md rounded-xl bg-white p-5 shadow-xl dark:bg-gray-900" role="dialog" aria-modal="true" aria-labelledby="unsaved-title"><h2 id="unsaved-title" class="text-lg font-semibold">Unsaved note</h2><p class="mt-2 text-sm text-gray-600 dark:text-gray-300">Save your changes before switching notes?</p><div class="mt-5 flex flex-wrap justify-end gap-2"><button class="rounded-lg px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800" on:click={() => pendingAction = null}>Keep editing</button><button class="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-700" on:click={discardAndContinue}>Discard</button><button class="rounded-lg bg-gray-900 px-3 py-2 text-sm text-white dark:bg-gray-100 dark:text-gray-900" on:click={saveAndContinue} disabled={saving}>Save and continue</button></div></section></div>
{/if}

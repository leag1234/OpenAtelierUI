<script>
	import { onMount, onDestroy, tick } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import { config, showSidebar } from '$lib/stores';
	import MenuLines from '$lib/components/icons/MenuLines.svelte';
	import fileSaver from 'file-saver';
	import { listNotes, searchNotes, getNote, createNote, updateNote, toggleNotePin } from '$lib/apis/notes';
	import { actionsBlocked, editableNote, focusTrapTargetIndex, formatNoteDate, isDirty, notePayload, originalNoteResponse, resolveSave, safeNoteFilename, saveStatus } from '$lib/apis/notes/state.js';

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
	let opening = false;
	let pinning = false;
	let status = '';
	let pendingAction = null;
	let dialogElement;
	let keepEditingButton;
	let dialogReturnFocus;
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
		opening = true;
		status = 'Loading note…';
		try {
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
		} finally {
			if (version === selectionVersion) opening = false;
		}
	};

	const startNew = () => {
		selectionVersion++;
		opening = false;
		selected = null;
		newNoteMode = true;
		title = '';
		text = '';
		baseline = { title: '', text: '' };
		editable = true;
		status = '';
	};

	const requestAction = (action) => {
		if (actionsBlocked({ saving, opening, pinning })) return;
		if (dirty) {
			dialogReturnFocus = typeof document !== 'undefined' ? document.activeElement : null;
			pendingAction = action;
			focusUnsavedDialog();
		}
		else action();
	};

	const focusUnsavedDialog = async () => {
		await tick();
		if (pendingAction) keepEditingButton?.focus();
	};
	const restoreDialogFocus = async () => {
		const target = dialogReturnFocus;
		dialogReturnFocus = null;
		await tick();
		if (target?.isConnected) target.focus();
	};
	const closeUnsavedDialog = () => {
		pendingAction = null;
		restoreDialogFocus();
	};
	const containDialogFocus = (event) => {
		if (pendingAction && dialogElement && !dialogElement.contains(event.target)) keepEditingButton?.focus();
	};
	const trapDialogTab = (event) => {
		if (!pendingAction || event.key !== 'Tab' || !dialogElement) return;
		const focusable = [...dialogElement.querySelectorAll('button:not([disabled])')];
		const targetIndex = focusTrapTargetIndex(focusable.indexOf(document.activeElement), focusable.length, event.shiftKey);
		if (targetIndex === -1) {
			event.preventDefault();
			dialogElement.focus();
		} else if (targetIndex !== null) {
			event.preventDefault();
			focusable[targetIndex].focus();
		}
	};

	const discardAndContinue = () => {
		if (saving || opening || pinning) return;
		const action = pendingAction;
		pendingAction = null;
		if (action) action();
		restoreDialogFocus();
	};

	const save = async () => {
		if (!editable || actionsBlocked({ saving, opening, pinning }) || !title.trim()) return false;
		saving = true;
		status = 'Saving…';
		const version = selectionVersion;
		const draft = { selected, title, text, baseline, editable };
		try {
			const payload = notePayload(title, text, selected, !selected || text !== baseline.text);
			const saved = selected
				? await updateNote(token(), selected.id, payload)
				: await createNote(token(), payload);
			if (version !== selectionVersion) return false;
			const result = resolveSave(draft, saved);
			selected = result.selected;
			baseline = result.baseline;
			title = result.baseline.title;
			const listRefreshed = await refreshList(1, appliedQuery);
			status = saveStatus(listRefreshed);
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
		if (actionsBlocked({ saving, opening, pinning })) return;
		await refreshList(1, query.trim());
	};

	const pin = async () => {
		if (!selected || actionsBlocked({ saving, opening, pinning })) return;
		const id = selected.id;
		pinning = true;
		try {
			const updated = await toggleNotePin(token(), id);
			if (selected?.id === id) selected = { ...selected, is_pinned: updated.is_pinned };
			notes = notes.map((note) => note.id === id ? { ...note, is_pinned: updated.is_pinned } : note);
		} catch (error) { setFailure(error); }
		finally { pinning = false; }
	};

	const downloadOriginal = () => {
		if (!selected) return;
		const raw = originalNoteResponse(selected);
		if (raw === null) {
			status = 'Reopen this note before downloading its original JSON.';
			return;
		}
		const blob = new Blob([raw], { type: 'application/json' });
		fileSaver.saveAs(blob, safeNoteFilename());
	};

	const beforeUnload = (event) => {
		if (!dirty) return;
		event.preventDefault();
		event.returnValue = '';
	};
	const onKeyDown = (event) => {
		if (pendingAction && event.key === 'Escape') {
			event.preventDefault();
			if (!actionsBlocked({ saving, opening, pinning })) closeUnsavedDialog();
			return;
		}
		trapDialogTab(event);
		if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && editable) {
			event.preventDefault();
			save();
		}
	};

	beforeNavigate(({ cancel }) => {
		if (dirty && typeof window !== 'undefined' && !window.confirm('You have unsaved note changes. Leave this page and discard them?')) cancel();
	});

	onMount(() => {
		if (typeof window === 'undefined') return;
		window.addEventListener('beforeunload', beforeUnload);
		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('focusin', containDialogFocus);
		if ($config?.features?.openatelier_private_notes) refreshList(1, '');
	});
	onDestroy(() => {
		selectionVersion++;
		if (typeof window === 'undefined') return;
		window.removeEventListener('beforeunload', beforeUnload);
		window.removeEventListener('keydown', onKeyDown);
		window.removeEventListener('focusin', containDialogFocus);
	});
</script>

<svelte:head><title>Private Notes · {$config?.name ?? 'OpenAtelierUI'}</title></svelte:head>

<main class="relative flex h-screen max-h-[100dvh] min-w-0 w-full max-w-full flex-col overflow-x-hidden overflow-y-auto bg-white text-gray-900 transition-width duration-200 ease-in-out {$showSidebar ? 'md:max-w-[calc(100%-260px)]' : ''} dark:bg-gray-950 dark:text-gray-100">
	<div class="w-full min-w-0 max-w-full px-4 py-6 sm:px-6 lg:px-8">
		<header class="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-gray-200 pb-4 dark:border-gray-800">
			<div class="flex items-start gap-2"><button class="mt-1 rounded-lg p-2 text-gray-600 hover:bg-gray-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:text-gray-300 dark:hover:bg-gray-900 {$showSidebar ? 'md:hidden' : ''}" on:click={() => showSidebar.set(!$showSidebar)} aria-label="Toggle sidebar"><MenuLines /></button><div><p class="text-xs font-semibold uppercase tracking-wide text-gray-500">Private workspace</p><h1 class="mt-1 text-2xl font-semibold">Notes</h1><p class="mt-1 text-sm text-gray-600 dark:text-gray-300">Only you can read these notes.</p></div></div>
			{#if $config?.features?.openatelier_private_notes}<button class="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900" on:click={() => requestAction(startNew)} disabled={actionsBlocked({ saving, opening, pinning })}>New note</button>{/if}
		</header>
		{#if !$config?.features?.openatelier_private_notes}
			<section class="rounded-xl border border-gray-200 p-6 dark:border-gray-800" role="status"><h2 class="font-semibold">Private notes are unavailable</h2><p class="mt-2 text-sm text-gray-600 dark:text-gray-300">This account cannot open the private notes workspace. Your chats remain available.</p><a class="mt-4 inline-flex rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900" href="/">Return to chat</a></section>
		{:else}
			{#if accountRefused}<p class="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100" role="status">This account cannot access private notes. <a class="underline" href="/">Return to chat</a></p>{/if}
			{#if status}<p class="mb-3 rounded-lg bg-gray-100 px-3 py-2 text-sm dark:bg-gray-900" role="status">{status}</p>{/if}
			{#if loading}<p class="mb-3 text-sm text-gray-500" role="status">Loading notes…</p>{/if}
			<div class="grid min-h-[65vh] grid-cols-1 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800 lg:grid-cols-[18rem_minmax(0,1fr)]">
				<aside class="border-b border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-900/40 lg:border-b-0 lg:border-r">
					<form class="flex gap-2" on:submit={applySearch}><label class="sr-only" for="note-search">Search note titles</label><input id="note-search" bind:value={query} maxlength="256" disabled={actionsBlocked({ saving, opening, pinning })} class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950" placeholder="Search titles"/><button class="rounded-lg border border-gray-300 px-3 text-sm hover:bg-white disabled:opacity-40 dark:border-gray-700 dark:hover:bg-gray-800" disabled={actionsBlocked({ saving, opening, pinning })}>Search</button></form>
					<div class="mt-3 max-h-80 space-y-1 overflow-y-auto lg:max-h-[55vh]" aria-label="Your notes">
						{#each notes as note (note.id)}<button class="w-full rounded-lg px-3 py-2 text-left hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-50 dark:hover:bg-gray-800 {selected?.id === note.id ? 'bg-white shadow-sm dark:bg-gray-800' : ''}" on:click={() => requestAction(() => openNote(note.id))} disabled={actionsBlocked({ saving, opening, pinning })}><span class="block truncate text-sm font-medium">{note.title || 'Untitled note'}</span><span class="mt-1 flex items-center gap-2 text-xs text-gray-500">{#if note.is_pinned}<span>Pinned</span>{/if}<span>{formatNoteDate(note.updated_at)}</span></span></button>{/each}
						{#if !loading && notes.length === 0}<p class="px-3 py-4 text-sm text-gray-500">{appliedQuery ? 'No matching titles.' : 'No saved notes yet.'}</p>{/if}
					</div>
					<div class="mt-3 flex items-center justify-between border-t border-gray-200 pt-3 text-xs text-gray-500 dark:border-gray-800"><span>{total === null ? `Page ${page}` : `${total} ${total === 1 ? 'note' : 'notes'}`}</span><div class="flex gap-2"><button class="rounded px-2 py-1 hover:bg-white disabled:opacity-40 dark:hover:bg-gray-800" on:click={() => refreshList(Math.max(1, page - 1), appliedQuery)} disabled={page <= 1 || loading || actionsBlocked({ saving, opening, pinning })}>Previous</button><button class="rounded px-2 py-1 hover:bg-white disabled:opacity-40 dark:hover:bg-gray-800" on:click={() => refreshList(page + 1, appliedQuery)} disabled={loading || notes.length < 32 || actionsBlocked({ saving, opening, pinning })}>Next</button></div></div>
				</aside>
				<section class="flex min-w-0 flex-col p-4 sm:p-6" aria-label="Note editor">
					{#if selected && !editable}
						<div class="flex flex-wrap items-center justify-between gap-3"><div><p class="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">Read-only note data</p><h2 class="mt-1 text-xl font-semibold">{selected.title}</h2></div><button class="rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-900" on:click={downloadOriginal} disabled={opening}>Download note JSON</button></div>
						<p class="mt-3 max-w-prose text-sm leading-6 text-gray-600 dark:text-gray-300">This note has content the plain-text editor cannot safely preserve. Nothing has been changed. Download the original JSON if you need to inspect or keep every field.</p>
					{:else if selected || newNoteMode}
						<div class="flex flex-wrap items-center justify-between gap-3"><div><p class="text-xs font-semibold uppercase tracking-wide text-gray-500">Private note</p><h2 class="mt-1 text-lg font-semibold">{selected ? 'Edit note' : 'New note'}</h2></div>{#if selected}<button class="rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900" on:click={pin} disabled={actionsBlocked({ saving, opening, pinning })}>{pinning ? 'Updating…' : selected.is_pinned ? 'Unpin' : 'Pin'}</button>{/if}</div>
						<label class="mt-5 text-sm font-medium" for="note-title">Title</label><input id="note-title" bind:value={title} disabled={actionsBlocked({ saving, opening, pinning })} class="mt-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-base dark:border-gray-700 dark:bg-gray-950" placeholder="A title for this note"/>
						<label class="mt-4 text-sm font-medium" for="note-text">Note</label><textarea id="note-text" bind:value={text} disabled={actionsBlocked({ saving, opening, pinning })} class="mt-1 min-h-64 flex-1 resize-y rounded-lg border border-gray-300 bg-white px-3 py-3 text-sm leading-6 dark:border-gray-700 dark:bg-gray-950" placeholder="Write a plan, reminder, or idea…" spellcheck="true"></textarea>
						<div class="mt-4 flex flex-wrap items-center justify-between gap-3"><span class="text-sm text-gray-500" aria-live="polite">{dirty ? 'Unsaved changes' : newNoteMode && !selected ? 'Not saved yet' : status || 'Saved'}</span><button class="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900" on:click={save} disabled={!dirty || actionsBlocked({ saving, opening, pinning }) || !title.trim()}>{saving ? 'Saving…' : 'Save note'}</button></div>
					{:else}<div class="m-auto max-w-sm text-center"><h2 class="text-lg font-semibold">Your notes, in one private place</h2><p class="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">Save a workshop plan or reminder, then find it again when you need it.</p><button class="mt-4 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900" on:click={() => requestAction(startNew)} disabled={actionsBlocked({ saving, opening, pinning })}>Create a note</button></div>{/if}
				</section>
			</div>
		{/if}
	</div>
</main>

{#if pendingAction}
	<div class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" role="presentation"><section bind:this={dialogElement} class="w-full max-w-md rounded-xl bg-white p-5 shadow-xl dark:bg-gray-900" role="dialog" aria-modal="true" aria-labelledby="unsaved-title" tabindex="-1"><h2 id="unsaved-title" class="text-lg font-semibold">Unsaved note</h2><p class="mt-2 text-sm text-gray-600 dark:text-gray-300">Save your changes before switching notes?</p><div class="mt-5 flex flex-wrap justify-end gap-2"><button bind:this={keepEditingButton} class="rounded-lg px-3 py-2 text-sm hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800" on:click={closeUnsavedDialog} disabled={saving || opening || pinning}>Keep editing</button><button class="rounded-lg border border-gray-300 px-3 py-2 text-sm disabled:opacity-50 dark:border-gray-700" on:click={discardAndContinue} disabled={saving || opening || pinning}>Discard</button><button class="rounded-lg bg-gray-900 px-3 py-2 text-sm text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900" on:click={saveAndContinue} disabled={saving || opening || pinning}>Save and continue</button></div></section></div>
{/if}

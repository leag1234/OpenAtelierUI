<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { activeResponseChatId, chatId, chats, config, showSidebar, user } from '$lib/stores';
	import { nativeMenuTriggerContract } from '$lib/actions/nativeMenuTriggerContract';
	import UserMenu from './UserMenu.svelte';

	const i18n = getContext('i18n');

	export let webuiName = 'OpenAtelierUI';
	export let expanded = false;
	export let mobile = false;
	export let onCreateFolder = () => {};
	export let folderItems: Record<string, any> = {};

	let foldersExpanded = false;
	let searchOpen = false;
	let searchQuery = '';
	let searchInput: HTMLInputElement | null = null;
	const contentSlotAttribute = { slot: 'content' };
	let visibleChats: any[] = [];
	let linkPreviewChatId: string | null = null;
	let linkPreviewTimer: ReturnType<typeof setTimeout> | null = null;
	let linkPreviewLeft = 0;
	let linkPreviewTop = 0;
	const foldersExpandedStorageKey = 'openatelierui-folders-expanded';
	$: visibleChats = ($chats ?? []).filter((chat) =>
		searchQuery ? chat.title?.toLowerCase().includes(searchQuery.toLowerCase()) : true
	);

	const portalToBody = (node: HTMLElement) => {
		document.body.appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	};

	const positionLinkPreview = (id: string) => {
		const index = visibleChats.findIndex((chat) => chat.id === id);
		const trigger = index < 0 ? null : document.getElementById(`bits-c${visibleChats.length - index}`);
		if (!trigger) return;
		const bounds = trigger.getBoundingClientRect();
		linkPreviewLeft = Math.max(8, Math.min(window.innerWidth - 264, bounds.right + 8));
		linkPreviewTop = Math.max(8, Math.min(window.innerHeight - 72, bounds.top));
	};

	const openLinkPreview = (id: string, delay = 300) => {
		if (linkPreviewTimer) clearTimeout(linkPreviewTimer);
		linkPreviewTimer = setTimeout(() => {
			positionLinkPreview(id);
			linkPreviewChatId = id;
			linkPreviewTimer = null;
		}, delay);
	};

	const closeLinkPreview = (id: string, delay = 120) => {
		if (linkPreviewTimer) clearTimeout(linkPreviewTimer);
		linkPreviewTimer = setTimeout(() => {
			if (linkPreviewChatId === id) linkPreviewChatId = null;
			linkPreviewTimer = null;
		}, delay);
	};

	const updateLinkPreviewPosition = () => {
		if (linkPreviewChatId) positionLinkPreview(linkPreviewChatId);
	};

	const toggleFolders = () => {
		document.getElementById('chat-input')?.blur();
		foldersExpanded = !foldersExpanded;
		localStorage.setItem(foldersExpandedStorageKey, String(foldersExpanded));
	};

	onMount(() => {
		foldersExpanded = localStorage.getItem(foldersExpandedStorageKey) === 'true';
		window.addEventListener('resize', updateLinkPreviewPosition);
		window.addEventListener('scroll', updateLinkPreviewPosition, true);
	});

	onDestroy(() => {
		if (linkPreviewTimer) clearTimeout(linkPreviewTimer);
		window.removeEventListener('resize', updateLinkPreviewPosition);
		window.removeEventListener('scroll', updateLinkPreviewPosition, true);
	});
</script>

<div
	id={expanded ? 'sidebar' : undefined}
	role="navigation"
	aria-label="Chat history"
	aria-hidden={expanded ? 'false' : 'true'}
	data-state={expanded ? 'true' : 'false'}
	inert={expanded ? undefined : true}
	style={expanded
		? mobile
			? 'width: 350px; --sidebar-width: 350px; transform: translateX(0%); transition: none;'
			: 'width: var(--sidebar-width); transform: translateX(0%); transition: none;'
		: 'width: 0px; transform: translateX(-100%); transition: none;'}
	class="h-screen max-h-[100dvh] min-h-screen select-none bg-gray-50 dark:bg-gray-950 z-50 {expanded ? '' : 'pointer-events-none'} shrink-0 text-gray-700 dark:text-gray-300 text-[0.8125rem] leading-5 fixed top-0 left-0 overflow-x-hidden"
>
	<div class="my-auto flex flex-col justify-between h-screen max-h-[100dvh] w-[var(--sidebar-width)] overflow-x-hidden scrollbar-hidden z-50 border-e border-gray-50 dark:border-gray-850/30">
		<div class="sidebar {mobile ? 'px-0.75 pt-1 pb-1 -mb-2' : 'px-1 pt-0.5 pb-0.5'} flex shrink-0 justify-between space-x-1 text-gray-600 dark:text-gray-400 sticky top-0 z-10">
			<a href="/" draggable="false" aria-label={$i18n.t('New Chat')} class="flex items-center rounded-xl size-8.5 {mobile ? 'h-full' : 'shrink-0'} justify-center hover:bg-gray-100 dark:hover:bg-gray-900 transition no-drag-region">
				<img crossorigin="anonymous" class="sidebar-new-chat-icon size-5 rounded-full" alt="" src="/static/favicon.png" />
			</a>
			<a href="/" class="flex min-w-0 flex-1 px-0.5">
				<div id="sidebar-webui-name" class="self-center truncate font-normal text-gray-700 dark:text-gray-200">{webuiName}</div>
			</a>
			<div class="flex shrink-0 items-center gap-px">
				<div class="flex">
					<button type="button" data-inbox-trigger="" aria-controls="sidebar-notifications" aria-label={$i18n.t('Notifications')} aria-pressed="false" class="flex size-[1.875rem] shrink-0 items-center justify-center rounded-lg text-gray-600 dark:text-gray-400 transition hover:bg-gray-100 dark:hover:bg-gray-900">
						<span class="relative flex size-4 items-center justify-center">
						<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" class="size-4"><path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" /></svg>
						</span>
					</button>
				</div>
				<div class="flex">
					<button aria-label={$i18n.t('Close navigation')} on:click={() => showSidebar.set(false)} class="flex size-[1.875rem] justify-center items-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-900 transition cursor-[w-resize]">
						<div class="self-center">
							<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" class="size-4">
								<path d="M19 21L5 21C3.89543 21 3 20.1046 3 19L3 5C3 3.89543 3.89543 3 5 3L19 3C20.1046 3 21 3.89543 21 5L21 19C21 20.1046 20.1046 21 19 21Z" />
								<path d="M9.5 21V3" />
								<path d="M5.5 10L7.25 12L5.5 14" />
							</svg>
						</div>
					</button>
				</div>
			</div>
			<div class="invisible sidebar-bg-gradient-to-b bg-linear-to-b from-gray-50 dark:from-gray-950 to-transparent from-50% pointer-events-none absolute inset-0 -z-10 -mb-6"></div>
		</div>

		<section id="sidebar-notifications" class="flex min-h-0 flex-1 flex-col pt-2.5" aria-label={$i18n.t('Notifications')} style="display: none;">
			<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-1.5 pb-3 flex flex-col">
				<div class="flex flex-1 items-center justify-center px-4 py-6 text-gray-500 dark:text-gray-400">
					<div class="flex justify-center text-center">
						<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="size-4">
							<style>
								.spinner_ajPY { transform-origin: center; animation: spinner_AtaB 0.75s infinite linear; }
								@keyframes spinner_AtaB { 100% { transform: rotate(360deg); } }
							</style>
							<path d="M12,1A11,11,0,1,0,23,12,11,11,0,0,0,12,1Zm0,19a8,8,0,1,1,8-8A8,8,0,0,1,12,20Z" opacity=".25" />
							<path d="M10.14,1.16a11,11,0,0,0-9,8.92A1.59,1.59,0,0,0,2.46,12,1.52,1.52,0,0,0,4.11,10.7a8,8,0,0,1,6.66-6.61A1.42,1.42,0,0,0,12,2.69h0A1.57,1.57,0,0,0,10.14,1.16Z" class="spinner_ajPY" />
						</svg>
					</div>
				</div>
			</div>
		</section>

		<div class="relative flex flex-col flex-1 overflow-y-auto scrollbar-hidden space-y-1.5 {mobile ? 'pt-1.5' : ''} pb-2.5">
			<div class="pb-1">
				<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
					<a id="sidebar-new-chat-button" href="/" draggable="false" aria-label={$i18n.t('New Chat')} class="group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none">
						<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="size-4" stroke-width="1.5"><path d="M14.3632 5.65156L15.8431 4.17157C16.6242 3.39052 17.8905 3.39052 18.6716 4.17157L20.0858 5.58579C20.8668 6.36683 20.8668 7.63316 20.0858 8.41421L18.6058 9.8942M14.3632 5.65156L4.74749 15.2672C4.41542 15.5993 4.21079 16.0376 4.16947 16.5054L3.92738 19.2459C3.87261 19.8659 4.39148 20.3848 5.0115 20.33L7.75191 20.0879C8.21972 20.0466 8.65806 19.8419 8.99013 19.5099L18.6058 9.8942M14.3632 5.65156L18.6058 9.8942" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /></svg></div>
						<div class="flex flex-1 self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">{$i18n.t('New Chat')}</div></div>
						<span class="hidden md:inline-flex items-center self-center hover-reveal"><span class="inline-flex min-h-[1.125rem] shrink-0 items-center justify-center rounded-full bg-gray-100 px-[0.3125rem] py-0.5 text-[0.625rem] font-medium leading-none text-gray-500 dark:bg-white/6 dark:text-gray-400">N</span></span>
					</a>
				</div>
				<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
					<button id="sidebar-search-button" draggable="false" aria-label={$i18n.t('Search')} on:click={async () => { searchOpen = !searchOpen; await tick(); searchInput?.focus(); }} class="group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none">
						<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="size-4" stroke-width="1.5"><path d="M17 17L21 21" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 11C3 15.4183 6.58172 19 11 19C13.213 19 15.2161 18.1015 16.6644 16.6493C18.1077 15.2022 19 13.2053 19 11C19 6.58172 15.4183 3 11 3C6.58172 3 3 6.58172 3 11Z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /></svg></div>
						<div class="flex flex-1 self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">{$i18n.t('Search')}</div></div>
						<span class="hidden md:inline-flex items-center self-center hover-reveal"><span class="inline-flex min-h-[1.125rem] shrink-0 items-center justify-center rounded-full bg-gray-100 px-[0.3125rem] py-0.5 text-[0.625rem] font-medium leading-none text-gray-500 dark:bg-white/6 dark:text-gray-400">S</span></span>
					</button>
				</div>
				{#if searchOpen}
					<div class="px-1 pt-1"><input bind:this={searchInput} bind:value={searchQuery} aria-label={$i18n.t('Search Chats')} class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-1.5 outline-none dark:border-gray-800" on:keydown={(event) => { if (event.key === 'Escape') { searchQuery = ''; searchOpen = false; } }} /></div>
				{/if}

				<div id="pinned-menu-items-list">
					{#if $config?.features?.openatelier_private_notes}
						<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300" data-id="notes">
							<a id="sidebar-notes-button" href="/notes" draggable="false" aria-label={$i18n.t('Notes')} class="grow flex items-center space-x-2 rounded-xl px-2 py-1.5 transition hover:bg-gray-100 dark:hover:bg-gray-900">
								<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-4"><path d="M5 3h14v18H5z" stroke="currentColor" stroke-linecap="round" /><path d="M8 7h8" stroke="currentColor" stroke-linecap="round" /><path d="M8 11h8" stroke="currentColor" stroke-linecap="round" /><path d="M8 15h5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /><path d="M8 19h4" stroke="currentColor" stroke-linecap="round" /></svg></div>
								<div class="flex self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">{$i18n.t('Notes')}</div></div>
							</a>
						</div>
					{/if}
					{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
						<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300" data-id="workspace">
							<a id="sidebar-workspace-button" href="/workspace" draggable="false" aria-label={$i18n.t('Workspace')} class="grow flex items-center space-x-2 rounded-xl px-2 py-1.5 transition hover:bg-gray-100 dark:hover:bg-gray-900">
								<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-4"><path d="M4 4h6v6H4z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /><path d="M14 4h6v6h-6z" stroke="currentColor" stroke-width="1.5" /><path d="M4 14h6v6H4z" stroke="currentColor" stroke-width="1.5" /><path d="M17 14v6" stroke="currentColor" stroke-width="1.5" /></svg></div>
								<div class="flex self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">{$i18n.t('Workspace')}</div></div>
							</a>
						</div>
					{/if}
				</div>
			</div>

			<div class="relative">
				<div id="" class="w-full">
					<div class="w-full cursor-pointer"><div><div class="flex items-start justify-between"><div class="flex items-center justify-between h-6 w-full pl-3.5 pr-1.5 shrink-0"><h2 class="flex flex-1 h-full"><button id="sidebar-folders-button" type="button" aria-expanded={foldersExpanded} aria-controls="sidebar-folders-content" on:click={toggleFolders} class="group flex flex-1 h-full items-center gap-1 text-left text-xs text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400 transition-colors duration-100"><span>{$i18n.t('Folders')}</span><span class="flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-all duration-100"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-[0.6875rem]"><path d="m9 18 6-6-6-6" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /></svg></span></button></h2><div class="contents"></div><button type="button" aria-label={$i18n.t('New Folder')} on:click={onCreateFolder} class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"><div class="flex"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-3.5"><path d="M12 5v14m-7-7h14" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /></svg></div></button></div></div></div></div>
					{#if foldersExpanded}<div style="overflow: hidden;"><div {...contentSlotAttribute} id="sidebar-folders-content" class="w-full px-1.5">
						{#each Object.values(folderItems).filter((folder) => folder?.parent_id == null) as folder}
							<button type="button" class="w-full rounded-xl px-2 py-1.5 text-left hover:bg-gray-100 dark:hover:bg-gray-900">{folder.name}</button>
						{/each}
					</div></div>{/if}
				</div>
			</div>

			<div class="relative">
				<div id="" class="w-full">
					<div class="w-full cursor-pointer"><div><div class="flex items-start justify-between"><div class="flex items-center justify-between h-6 w-full pl-3.5 pr-1.5 shrink-0"><h2 class="flex flex-1 h-full"><button id="sidebar-chats-button" type="button" aria-expanded="true" aria-controls="sidebar-chats-content" class="group flex flex-1 h-full items-center gap-1 text-left text-xs text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400 transition-colors duration-100"><span>{$i18n.t('Chats')}</span><span class="flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-all duration-100"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-[0.6875rem]"><path d="m9 18 6-6-6-6" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /></svg></span></button></h2><div class="contents"><span><div class="flex"><button type="button" aria-expanded="false" aria-haspopup="menu" aria-label={$i18n.t('Chat list actions')} class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"><svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-3.5"><path d="M12 5v14" fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /><path d="M5 12h14" fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /><path d="M7 7h10" fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" /></svg></button></div></span></div></div></div></div></div>
					<div>
						<div {...contentSlotAttribute} id="sidebar-chats-content" class="w-full px-1.5">
							<div class="flex-1 flex flex-col overflow-y-auto scrollbar-hidden">
								<div class="pt-1.5">
									<div role="list">
						{#each visibleChats as chat, index}
											<div role="listitem">
								{#if index === 0 || chat.time_range !== visibleChats[index - 1]?.time_range}
													<h3 class="w-full pl-2.5 text-xs text-gray-500 dark:text-gray-500 font-normal pb-1">
																{$i18n.t(chat.time_range ?? 'Today')}
													</h3>
												{/if}
												<div
													id="sidebar-chat-group"
													data-sidebar-chat-id={chat.id}
													data-sidebar-chat-readonly="false"
													class="w-full relative group"
													draggable="true"
												>
													<div
														id="sidebar-chat-item"
														class="w-full flex justify-between rounded-xl px-2 py-1.5 {$chatId === chat.id
															? 'bg-black/[0.035] dark:bg-white/[0.045] selected'
															: 'hover:bg-gray-100 dark:hover:bg-gray-900 group-hover:bg-gray-100 dark:group-hover:bg-gray-900'} whitespace-nowrap text-ellipsis transition"
													>
														<a
													id={mobile ? undefined : `bits-c${visibleChats.length - index}`}
															class="flex min-w-0 flex-1 {mobile ? '' : '-my-1.5 py-1.5'} items-center"
															href="/c/{chat.id}"
															aria-current={$chatId === chat.id ? 'page' : undefined}
															aria-expanded={mobile ? undefined : linkPreviewChatId === chat.id}
															aria-haspopup={mobile ? undefined : 'dialog'}
															data-link-preview-trigger={mobile ? undefined : ''}
															data-state={mobile ? undefined : linkPreviewChatId === chat.id ? 'open' : 'closed'}
															role={mobile ? undefined : 'button'}
															draggable="false"
															on:mouseenter={() => !mobile && openLinkPreview(chat.id)}
															on:mouseleave={() => !mobile && closeLinkPreview(chat.id)}
															on:focus={() => !mobile && openLinkPreview(chat.id, 0)}
															on:blur={() => !mobile && closeLinkPreview(chat.id)}
															on:keydown={(event) => {
																if (event.key === 'Escape') closeLinkPreview(chat.id, 0);
															}}
														>
															<div class="flex self-center flex-1 w-full min-w-0">
																<div dir="auto" class="text-left self-center overflow-hidden w-full h-5 truncate {$chatId === chat.id ? 'pr-12' : ''}">
																	{chat.title}
																</div>
															</div>
																{#if $activeResponseChatId === chat.id}
																	<div class="pr-2 self-center shrink-0">
																<div class="flex items-center justify-center size-3">
																			<div class="flex justify-center text-center">
																				<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="size-3">
																					<style>
																						.spinner_ajPY { transform-origin: center; animation: spinner_AtaB 0.75s infinite linear; }
																						@keyframes spinner_AtaB { 100% { transform: rotate(360deg); } }
																					</style>
																					<path d="M12,1A11,11,0,1,0,23,12,11,11,0,0,0,12,1Zm0,19a8,8,0,1,1,8-8A8,8,0,0,1,12,20Z" opacity=".25" />
																					<path d="M10.14,1.16a11,11,0,0,0-9,8.92A1.59,1.59,0,0,0,2.46,12,1.52,1.52,0,0,0,4.11,10.7a8,8,0,0,1,6.66-6.61A1.42,1.42,0,0,0,12,2.69h0A1.57,1.57,0,0,0,10.14,1.16Z" class="spinner_ajPY" />
																				</svg>
																			</div>
																		</div>
																	</div>
																{/if}
															<span class="sr-only">a few seconds ago</span>
															{#if $chatId !== chat.id}
																<div class="shrink-0 self-center text-[0.625rem] text-gray-400 dark:text-gray-500 pl-2" aria-hidden="true">1m</div>
															{/if}
														</a>
														{#if linkPreviewChatId === chat.id}
															<div
																use:portalToBody
																id="sidebar-chat-preview-{chat.id}"
																role="dialog"
																aria-label={$i18n.t('Chat preview')}
																class="fixed z-[100] hidden w-64 rounded-xl border border-gray-200 bg-white p-3 text-left shadow-lg dark:border-gray-800 dark:bg-gray-900 md:block"
																style:left={`${linkPreviewLeft}px`}
																style:top={`${linkPreviewTop}px`}
																on:mouseenter={() => openLinkPreview(chat.id, 0)}
																on:mouseleave={() => closeLinkPreview(chat.id)}
															>
																<div class="truncate font-medium text-gray-800 dark:text-gray-100">
																	{chat.title}
																</div>
															</div>
														{/if}
													</div>
													<div
														id="sidebar-chat-item-menu"
														class="{$chatId === chat.id ? 'selected' : 'hover-reveal'} absolute right-1 inset-y-0 mr-1.5 flex items-center"
													>
														<div class="flex self-center z-10 items-end">
															<span style="display: contents; cursor: pointer;">
																<div class="flex">
															<button type="button" name="sidebar-chat-menu" id="sidebar-chat-menu-{chat.id}" aria-label="Chat Menu" aria-haspopup="true" aria-expanded="false" class="flex size-5 items-center justify-center self-center dark:hover:text-white transition m-0">
																							<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="size-3.5" stroke-width="2">
																								<path d="M20 12.5C20.2761 12.5 20.5 12.2761 20.5 12C20.5 11.7239 20.2761 11.5 20 11.5C19.7239 11.5 19.5 11.7239 19.5 12C19.5 12.2761 19.7239 12.5 20 12.5Z" fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
																								<path d="M12 12.5C12.2761 12.5 12.5 12.2761 12.5 12C12.5 11.7239 12.2761 11.5 12 11.5C11.7239 11.5 11.5 11.7239 11.5 12C11.5 12.2761 11.7239 12.5 12 12.5Z" fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
																								<path d="M4 12.5C4.27614 12.5 4.5 12.2761 4.5 12C4.5 11.7239 4.27614 11.5 4 11.5C3.72386 11.5 3.5 11.7239 3.5 12C3.5 12.2761 3.72386 12.5 4 12.5Z" fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
																		</svg>
																	</button>
																</div>
															</span>
														</div>
													</div>
												</div>
											</div>
										{/each}
									</div>
								</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>

		<div class="px-1 pt-1 pb-1.5 sticky bottom-0 z-10 -mt-2 sidebar">
			<div class="sidebar-bg-gradient-to-t bg-linear-to-t from-gray-50 dark:from-gray-950 to-transparent from-50% pointer-events-none absolute inset-0 -z-10 -mt-6"></div>
			<div class="flex flex-col"><span><UserMenu role={$user?.role} let:builder><button {...builder} use:builder.action use:nativeMenuTriggerContract={{ enabled: true, removeGeneratedId: true }} type="button" tabindex={expanded ? 0 : -1} aria-haspopup="menu" class="flex items-center rounded-xl py-1.5 px-1.5 w-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"><div class="self-center mr-3 relative flex-shrink-0"><img src={$user?.profile_image_url ?? '/static/favicon.png'} alt={$i18n.t('Open User Profile Menu')} aria-label={$i18n.t('Open User Profile Menu')} class="size-5.5 object-cover rounded-full" /><div class="absolute -bottom-0.5 -right-0.5"><span class="relative flex size-2.5"><span class="relative inline-flex size-2.5 rounded-full bg-green-500 border-2 border-white dark:border-gray-900"></span></span></div></div><div class="self-center font-normal truncate">{$user?.name ?? 'User'}</div></button></UserMenu></span></div>
		</div>
	</div>
</div>

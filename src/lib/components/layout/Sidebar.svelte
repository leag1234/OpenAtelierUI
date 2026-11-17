<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';

	import { goto } from '$app/navigation';
	import {
		user,
		WEBUI_NAME,
		chats,
		settings,
		showSettings,
		chatId,
		tags,
		showSidebar,
		mobile,
		showArchivedChats,
		pinnedChats,
		scrollPaginationEnabled,
		currentChatPage,
		temporaryChatEnabled,
		channels,
		socket,
		config,
		isApp
	} from '$lib/stores';
	import { onMount, getContext, tick, onDestroy } from 'svelte';

	const i18n = getContext('i18n');

	import {
		deleteChatById,
		getChatList,
		getAllTags,
		getChatListBySearchText,
		createNewChat,
		getPinnedChatList,
		toggleChatPinnedStatusById,
		getChatPinnedStatusById,
		getChatById,
		updateChatFolderIdById,
		importChat
	} from '$lib/apis/chats';
	import { createNewFolder, getFolders, updateFolderParentIdById } from '$lib/apis/folders';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { sidebarStateMarker } from '$lib/utils/sidebarState';
	import { scheduleContainedNavigationFocus } from '$lib/utils/modalFocus';
	import { nativeMenuTriggerContract } from '$lib/actions/nativeMenuTriggerContract';

	import ArchivedChatsModal from './Sidebar/ArchivedChatsModal.svelte';
	import UserMenu from './Sidebar/UserMenu.svelte';
	import ChatItem from './Sidebar/ChatItem.svelte';
	import PinnedMenuItems from './Sidebar/PinnedMenuItems.svelte';
	import CurrentSidebarShadow from './Sidebar/CurrentSidebarShadow.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Loader from '../common/Loader.svelte';
	import AddFilesPlaceholder from '../AddFilesPlaceholder.svelte';
	import SearchInput from './Sidebar/SearchInput.svelte';
	import Folder from '../common/Folder.svelte';
	import Plus from '../icons/Plus.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Folders from './Sidebar/Folders.svelte';
	import { getChannels, createNewChannel } from '$lib/apis/channels';
	import ChannelModal from './Sidebar/ChannelModal.svelte';
	import ChannelItem from './Sidebar/ChannelItem.svelte';
	import PencilSquare from '../icons/PencilSquare.svelte';
	import Home from '../icons/Home.svelte';
	import Document from '../icons/Document.svelte';

	const BREAKPOINT = 768;

	let navElement;
	let sidebarWidth = 260;
    let resizing = false;
    const setSidebarWidth = (value) => {
        sidebarWidth = Math.max(200, Math.min(360, Number(value) || 260));
        document.documentElement.style.setProperty('--sidebar-width', `${sidebarWidth}px`);
        localStorage.setItem('sidebar-width', String(sidebarWidth));
    };
	let closeNavigationButton;
	let mobileFocusCleanup = null;
	let search = '';

	let shiftKey = false;

	let selectedChatId = null;
	let showDropdown = false;
	let showPinnedChat = true;

	let showCreateChannel = false;
	$: latestCompatibility = $config?.compatibility_profile === 'native';

	// Pagination variables
	let chatListLoading = false;
	let allChatsLoaded = false;

	let folders = {};
	let newFolderId = null;

	const initFolders = async () => {
		const folderList = await getFolders(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});

		folders = {};

		// First pass: Initialize all folder entries
		for (const folder of folderList) {
			// Ensure folder is added to folders with its data
			folders[folder.id] = { ...(folders[folder.id] || {}), ...folder };

			if (newFolderId && folder.id === newFolderId) {
				folders[folder.id].new = true;
				newFolderId = null;
			}
		}

		// Second pass: Tie child folders to their parents
		for (const folder of folderList) {
			if (folder.parent_id) {
				// Ensure the parent folder is initialized if it doesn't exist
				if (!folders[folder.parent_id]) {
					folders[folder.parent_id] = {}; // Create a placeholder if not already present
				}

				// Initialize childrenIds array if it doesn't exist and add the current folder id
				folders[folder.parent_id].childrenIds = folders[folder.parent_id].childrenIds
					? [...folders[folder.parent_id].childrenIds, folder.id]
					: [folder.id];

				// Sort the children by updated_at field
				folders[folder.parent_id].childrenIds.sort((a, b) => {
					return folders[b].updated_at - folders[a].updated_at;
				});
			}
		}
	};

	const createFolder = async (name = 'Untitled') => {
		if (name === '') {
			toast.error($i18n.t('Folder name cannot be empty.'));
			return;
		}

		const rootFolders = Object.values(folders).filter((folder) => folder.parent_id === null);
		if (rootFolders.find((folder) => folder.name.toLowerCase() === name.toLowerCase())) {
			// If a folder with the same name already exists, append a number to the name
			let i = 1;
			while (
				rootFolders.find((folder) => folder.name.toLowerCase() === `${name} ${i}`.toLowerCase())
			) {
				i++;
			}

			name = `${name} ${i}`;
		}

		// Add a dummy folder to the list to show the user that the folder is being created
		const tempId = uuidv4();
		folders = {
			...folders,
			tempId: {
				id: tempId,
				name: name,
				created_at: Date.now(),
				updated_at: Date.now()
			}
		};

		const res = await createNewFolder(localStorage.token, name).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			newFolderId = res.id;
			await initFolders();
		}
	};

	const initChannels = async () => {
		await channels.set(await getChannels(localStorage.token));
	};

	const initChatList = async () => {
		// Reset pagination variables
		tags.set(await getAllTags(localStorage.token));
		pinnedChats.set(await getPinnedChatList(localStorage.token));
		initFolders();

		currentChatPage.set(1);
		allChatsLoaded = false;

		const initialChats = search
			? await getChatListBySearchText(localStorage.token, search, $currentChatPage)
			: await getChatList(localStorage.token, $currentChatPage);
		await chats.set(initialChats);
		allChatsLoaded = initialChats.length === 0;

		// Enable pagination
		scrollPaginationEnabled.set(true);
	};

	const loadMoreChats = async () => {
		chatListLoading = true;

		currentChatPage.set($currentChatPage + 1);

		let newChatList = [];

		if (search) {
			newChatList = await getChatListBySearchText(localStorage.token, search, $currentChatPage);
		} else {
			newChatList = await getChatList(localStorage.token, $currentChatPage);
		}

		// once the bottom of the list has been reached (no results) there is no need to continue querying
		allChatsLoaded = newChatList.length === 0;
		await chats.set([...($chats ? $chats : []), ...newChatList]);

		chatListLoading = false;
	};

	let searchDebounceTimeout;

	const searchDebounceHandler = async () => {
		console.log('search', search);
		chats.set(null);

		if (searchDebounceTimeout) {
			clearTimeout(searchDebounceTimeout);
		}

		if (search === '') {
			await initChatList();
			return;
		} else {
			searchDebounceTimeout = setTimeout(async () => {
				allChatsLoaded = false;
				currentChatPage.set(1);
				await chats.set(await getChatListBySearchText(localStorage.token, search));

				if ($chats.length === 0) {
					tags.set(await getAllTags(localStorage.token));
				}
			}, 1000);
		}
	};

	const importChatHandler = async (items, pinned = false, folderId = null) => {
		console.log('importChatHandler', items, pinned, folderId);
		for (const item of items) {
			console.log(item);
			if (item.chat) {
				await importChat(localStorage.token, item.chat, item?.meta ?? {}, pinned, folderId);
			}
		}

		initChatList();
	};

	const inputFilesHandler = async (files) => {
		console.log(files);

		for (const file of files) {
			const reader = new FileReader();
			reader.onload = async (e) => {
				const content = e.target.result;

				try {
					const chatItems = JSON.parse(content);
					importChatHandler(chatItems);
				} catch {
					toast.error($i18n.t(`Invalid file format.`));
				}
			};

			reader.readAsText(file);
		}
	};

	const tagEventHandler = async (type, tagName, chatId) => {
		console.log(type, tagName, chatId);
		if (type === 'delete') {
			initChatList();
		} else if (type === 'add') {
			initChatList();
		}
	};

	let draggedOver = false;

	const onDragOver = (e) => {
		e.preventDefault();

		// Check if a file is being draggedOver.
		if (e.dataTransfer?.types?.includes('Files')) {
			draggedOver = true;
		} else {
			draggedOver = false;
		}
	};

	const onDragLeave = () => {
		draggedOver = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();
		console.log(e); // Log the drop event

		// Perform file drop check and handle it accordingly
		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);

			if (inputFiles && inputFiles.length > 0) {
				console.log(inputFiles); // Log the dropped files
				inputFilesHandler(inputFiles); // Handle the dropped files
			}
		}

		draggedOver = false; // Reset draggedOver status after drop
	};

	let touchstart;
	let touchend;

	function checkDirection() {
		const screenWidth = window.innerWidth;
		const swipeDistance = Math.abs(touchend.screenX - touchstart.screenX);
		if (touchstart.clientX < 40 && swipeDistance >= screenWidth / 8) {
			if (touchend.screenX < touchstart.screenX) {
				showSidebar.set(false);
			}
			if (touchend.screenX > touchstart.screenX) {
				showSidebar.set(true);
			}
		}
	}

	const onTouchStart = (e) => {
		touchstart = e.changedTouches[0];
		console.log(touchstart.clientX);
	};

	const onTouchEnd = (e) => {
		touchend = e.changedTouches[0];
		checkDirection();
	};

	const onKeyDown = (e) => {
		if (e.key === 'Shift') {
			shiftKey = true;
		}
	};

	const onKeyUp = (e) => {
		if (e.key === 'Shift') {
			shiftKey = false;
		}
	};

	const onFocus = () => {};

	const onBlur = () => {
		shiftKey = false;
		selectedChatId = null;
	};

	$: if (
		$mobile &&
		$showSidebar &&
		navElement &&
		closeNavigationButton &&
		!mobileFocusCleanup
	) {
		const navigation = navElement;
		mobileFocusCleanup = scheduleContainedNavigationFocus(
			navigation,
			closeNavigationButton,
			() => showSidebar.set(false),
			tick,
			() =>
				$mobile &&
				$showSidebar &&
				navElement === navigation &&
				navigation.isConnected &&
				closeNavigationButton?.isConnected
		);
	} else if ((!$mobile || !$showSidebar) && mobileFocusCleanup) {
		const cleanup = mobileFocusCleanup;
		mobileFocusCleanup = null;
		cleanup();
	}

	onMount(async () => {
		setSidebarWidth(localStorage.getItem('sidebar-width') || (latestCompatibility ? 245 : 260));
		showPinnedChat = localStorage?.showPinnedChat ? localStorage.showPinnedChat === 'true' : true;

		mobile.subscribe((value) => {
			if ($showSidebar && value) {
				showSidebar.set(false);
			}

			if ($showSidebar && !value) {
				const navElement = document.getElementsByTagName('nav')[0];
				if (navElement) {
					navElement.style['-webkit-app-region'] = 'drag';
				}
			}

			if (!$showSidebar && !value) {
				showSidebar.set(true);
			}
		});

		showSidebar.set(!$mobile ? localStorage.sidebar === 'true' : false);
		showSidebar.subscribe((value) => {
			localStorage.sidebar = value;

			// nav element is not available on the first render
			const navElement = document.getElementsByTagName('nav')[0];

			if (navElement) {
				if ($mobile) {
					if (!value) {
						navElement.style['-webkit-app-region'] = 'drag';
					} else {
						navElement.style['-webkit-app-region'] = 'no-drag';
					}
				} else {
					navElement.style['-webkit-app-region'] = 'drag';
				}
			}
		});

		await initChannels();
		await initChatList();

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);

		window.addEventListener('touchstart', onTouchStart);
		window.addEventListener('touchend', onTouchEnd);

		window.addEventListener('focus', onFocus);
		window.addEventListener('blur-sm', onBlur);

		const dropZone = document.getElementById('sidebar');

		dropZone?.addEventListener('dragover', onDragOver);
		dropZone?.addEventListener('drop', onDrop);
		dropZone?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		if (mobileFocusCleanup) {
			mobileFocusCleanup();
			mobileFocusCleanup = null;
		}
		window.removeEventListener('keydown', onKeyDown);
		window.removeEventListener('keyup', onKeyUp);

		window.removeEventListener('touchstart', onTouchStart);
		window.removeEventListener('touchend', onTouchEnd);

		window.removeEventListener('focus', onFocus);
		window.removeEventListener('blur-sm', onBlur);

		const dropZone = document.getElementById('sidebar');

		dropZone?.removeEventListener('dragover', onDragOver);
		dropZone?.removeEventListener('drop', onDrop);
		dropZone?.removeEventListener('dragleave', onDragLeave);
	});
</script>

<ArchivedChatsModal
	bind:show={$showArchivedChats}
	on:change={async () => {
		await initChatList();
	}}
/>

<ChannelModal
	bind:show={showCreateChannel}
	onSubmit={async ({ name, access_control }) => {
		const res = await createNewChannel(localStorage.token, {
			name: name,
			access_control: access_control
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			$socket.emit('join-channels', { auth: { token: $user?.token } });
			await initChannels();
			showCreateChannel = false;
		}
	}}
/>

<!-- svelte-ignore a11y-no-static-element-interactions -->

{#if latestCompatibility && $showSidebar}
	<div class="fixed md:hidden z-40 top-0 right-0 left-0 bottom-0 bg-black w-full min-h-screen h-screen flex justify-center overflow-hidden overscroll-contain" style="opacity: 0.6; transition: none;"></div>
	<button id="sidebar-new-chat-button" class="hidden"></button>
	<CurrentSidebarShadow
		webuiName={$WEBUI_NAME}
		expanded={true}
		mobile={$mobile}
		onCreateFolder={() => createFolder()}
		folderItems={folders}
	/>
	{#if !$mobile}
		<div class="relative flex items-center justify-center group border-r border-gray-50 dark:border-gray-850/30 hover:border-gray-200 dark:hover:border-gray-800 transition z-20 bg-transparent p-0 appearance-none">
			<div class="absolute -left-1.5 -right-1.5 -top-0 -bottom-0 z-20 cursor-col-resize bg-transparent"></div>
		</div>
	{/if}
{:else}
{#if latestCompatibility && $mobile && !$showSidebar}
	<button id="sidebar-new-chat-button" class="hidden"></button>
{/if}
{#if !$mobile || $showSidebar}
{#if !$showSidebar && !$mobile && latestCompatibility}
	<button type="button" class="hidden" aria-hidden="true" tabindex="-1"></button>
{/if}
<div id="sidebar" data-state={sidebarStateMarker($showSidebar)}
	role={!$showSidebar && !$mobile && latestCompatibility ? 'navigation' : undefined}
	aria-label={!$showSidebar && !$mobile && latestCompatibility ? $i18n.t('Chat history') : undefined}
	class={$showSidebar
		? 'relative shrink-0 h-screen max-h-[100dvh] w-[260px]'
		: latestCompatibility
			? 'w-[calc(42px*var(--app-text-scale,1))] shrink-0 pt-0.5 pb-1 px-1 flex flex-col justify-between text-gray-700 dark:text-gray-300 hover:bg-gray-50/30 dark:hover:bg-gray-800/30 h-full z-10 transition-all border-e-[0.5px] border-gray-50 dark:border-gray-850/30'
			: 'relative shrink-0 h-screen max-h-[100dvh] w-12'}
	style={$showSidebar ? `width:${sidebarWidth}px` : latestCompatibility ? undefined : 'width:48px !important'}>
{#if !$showSidebar && !$mobile}
	{#if latestCompatibility}
		<div role="presentation" class="flex flex-col flex-1 cursor-[e-resize]">
			<div class="pb-0.5">
				<div class="flex">
					<button
						id="sidebar-open-button"
						class="flex size-8.5 shrink-0 items-center justify-center transition group cursor-[e-resize]"
						aria-label={$i18n.t('Open Sidebar')}
						on:click={() => showSidebar.set(true)}
					>
						<div class="self-center relative flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
							<img class="sidebar-new-chat-icon size-5 rounded-full group-hover:hidden" alt="" src="{WEBUI_BASE_URL}/static/favicon.png" />
							<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" class="size-4 hidden group-hover:flex">
								<path d="M19 21L5 21C3.89543 21 3 20.1046 3 19L3 5C3 3.89543 3.89543 3 5 3L19 3C20.1046 3 21 3.89543 21 5L21 19C21 20.1046 20.1046 21 19 21Z" />
								<path d="M9.5 21V3" />
								<path d="M5.5 10L7.25 12L5.5 14" />
							</svg>
						</div>
					</button>
				</div>
			</div>

			<div class="-gap-0.5">
				<div><div class="flex">
					<a
						href="/"
						draggable="false"
						class="cursor-pointer flex size-8 items-center justify-center transition group"
						aria-label={$i18n.t('New Chat')}
						on:click={() => {
							selectedChatId = null;
							chatId.set('');
						}}
					>
						<div class="self-center flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
							<PencilSquare className="size-4" strokeWidth="1.5" pathStroke={true} />
						</div>
					</a>
				</div></div>

				<div><div class="flex">
					<button
						draggable="false"
						class="cursor-pointer flex size-8 items-center justify-center transition group"
						aria-label={$i18n.t('Search')}
						on:click={async () => {
							showSidebar.set(true);
							await tick();
							document.querySelector('#chat-search input')?.focus();
						}}
					>
						<div class="self-center flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
							<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="size-4" stroke-width="1.5">
								<path d="M17 17L21 21" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
								<path d="M3 11C3 15.4183 6.58172 19 11 19C13.213 19 15.2161 18.1015 16.6644 16.6493C18.1077 15.2022 19 13.2053 19 11C19 6.58172 15.4183 3 11 3C6.58172 3 3 6.58172 3 11Z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
							</svg>
						</div>
					</button>
				</div></div>

				{#if $config?.features?.openatelier_private_notes}
					<div><div class="flex">
						<a href="/notes" draggable="false" class="cursor-pointer flex size-8 items-center justify-center transition group" aria-label={$i18n.t('Notes')}>
							<div class="self-center flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
								<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-4">
									<path d="M5 3h14v18H5z" stroke="currentColor" stroke-linecap="round" />
									<path d="M8 7h8" stroke="currentColor" stroke-linecap="round" />
									<path d="M8 11h8" stroke="currentColor" stroke-linecap="round" />
									<path d="M8 15h5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
									<path d="M8 19h4" stroke="currentColor" stroke-linecap="round" />
								</svg>
							</div>
						</a>
					</div></div>
				{/if}

				{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
					<div><div class="flex">
						<a href="/workspace" draggable="false" class="cursor-pointer flex size-8 items-center justify-center transition group" aria-label={$i18n.t('Workspace')}>
							<div class="self-center flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
								<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="size-4" stroke-width="1.5">
									<path d="M13.9922 17H16.9922M19.9922 17H16.9922M16.9922 17V14M16.9922 17V20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />
									<path d="M4 9.4V4.6C4 4.26863 4.26863 4 4.6 4H9.4C9.73137 4 10 4.26863 10 4.6V9.4C10 9.73137 9.73137 10 9.4 10H4.6C4.26863 10 4 9.73137 4 9.4Z" stroke="currentColor" stroke-width="1.5" />
									<path d="M4 19.4V14.6C4 14.2686 4.26863 14 4.6 14H9.4C9.73137 14 10 14.2686 10 14.6V19.4C10 19.7314 9.73137 20 9.4 20H4.6C4.26863 20 4 19.7314 4 19.4Z" stroke="currentColor" stroke-width="1.5" />
									<path d="M14 9.4V4.6C14 4.26863 14.2686 4 14.6 4H19.4C19.7314 4 20 4.26863 20 4.6V9.4C20 9.73137 19.7314 10 19.4 10H14.6C14.2686 10 14 9.73137 14 9.4Z" stroke="currentColor" stroke-width="1.5" />
								</svg>
							</div>
						</a>
					</div></div>
				{/if}
			</div>
		</div>

		{#if $user !== undefined && $user !== null}
			<div><div><div class="flex justify-center items-center">
				<UserMenu role={$user?.role} className="max-w-[240px]" let:builder>
					<span><button {...builder} use:builder.action use:nativeMenuTriggerContract={{ enabled: true, removeGeneratedId: true }} type="button" class="cursor-pointer flex size-8.5 items-center justify-center transition group" aria-haspopup="menu" aria-label={$i18n.t('User menu')}>
						<div class="self-center relative flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg transition group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
							<img src={$user?.profile_image_url} class="size-5.5 object-cover rounded-full" alt={$i18n.t('Open User Profile Menu')} aria-label={$i18n.t('Open User Profile Menu')} />
							<div class="absolute -bottom-0.5 -right-0.5"><span class="relative flex size-2.5"><span class="relative inline-flex size-2.5 rounded-full bg-green-500 border-2 border-white dark:border-gray-900"></span></span></div>
						</div>
					</button></span>
				</UserMenu>
			</div></div></div>
		{/if}
	{:else}
	<nav
		id="collapsed-navigation-rail"
		aria-label={$i18n.t('Primary navigation')}
		class="relative z-40 flex h-screen max-h-[100dvh] w-12 shrink-0 flex-col items-center justify-between border-r border-gray-100 bg-gray-50 py-2 text-gray-600 dark:border-gray-900 dark:bg-gray-950 dark:text-gray-300"
	>
		<div class="flex flex-col items-center gap-1">
			<PinnedMenuItems compact={true} />
			<Tooltip content={$i18n.t('Open Sidebar')} placement="right">
				<button
					type="button"
					class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
					aria-label={$i18n.t('Open Sidebar')}
					on:click={() => showSidebar.set(true)}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="size-5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
					</svg>
				</button>
			</Tooltip>

			<Tooltip content={$i18n.t('New Chat')} placement="right">
				<a
					href="/"
					class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
					aria-label={$i18n.t('New Chat')}
					on:click={() => {
						selectedChatId = null;
						chatId.set('');
					}}
				>
					<PencilSquare className="size-5" strokeWidth="2" />
				</a>
			</Tooltip>

			<Tooltip content={$i18n.t('Search')} placement="right">
				<button
					type="button"
					class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
					aria-label={$i18n.t('Search')}
					on:click={async () => {
						showSidebar.set(true);
						await tick();
						document.querySelector('#chat-search input')?.focus();
					}}
				>
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-5">
						<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11ZM2 9a7 7 0 1 1 12.452 4.391l3.328 3.329a.75.75 0 1 1-1.06 1.06l-3.329-3.328A7 7 0 0 1 2 9Z" clip-rule="evenodd" />
					</svg>
				</button>
			</Tooltip>

			{#if $config?.features?.openatelier_private_notes}
				<Tooltip content={$i18n.t('Notes')} placement="right">
					<a href="/notes" class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900" aria-label={$i18n.t('Notes')}>
						<Document className="size-5" />
					</a>
				</Tooltip>
			{/if}

			{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
				<Tooltip content={$i18n.t('Workspace')} placement="right">
					<a href="/workspace" class="rounded-xl p-2 hover:bg-gray-100 dark:hover:bg-gray-900" aria-label={$i18n.t('Workspace')}>
						<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="size-5">
							<path stroke-linecap="round" stroke-linejoin="round" d="M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 8.25 3.75H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z" />
						</svg>
					</a>
				</Tooltip>
			{/if}
		</div>

		{#if $user !== undefined && $user !== null}
			<UserMenu role={$user?.role} className="max-w-[240px]" let:builder>
				<button {...builder} use:builder.action type="button" class="rounded-full p-1 hover:bg-gray-100 dark:hover:bg-gray-900" aria-haspopup="menu" aria-label={$i18n.t('User menu')}>
					<img src={$user?.profile_image_url} class="size-8 rounded-full object-cover" alt="User profile" />
				</button>
			</UserMenu>
		{/if}
	</nav>
	{/if}
{/if}

{#if $showSidebar}
	<div
		class=" {$isApp
			? ' ml-[4.5rem] md:ml-0'
			: ''} fixed md:hidden z-40 top-0 right-0 left-0 bottom-0 bg-black/60 w-full min-h-screen h-screen flex justify-center overflow-hidden overscroll-contain"
		on:mousedown={() => {
			showSidebar.set(!$showSidebar);
		}}
	/>
{/if}

{#if $showSidebar}
<div
	style={`width:${sidebarWidth}px;max-width:${sidebarWidth}px`}
	bind:this={navElement}
	id="expanded-navigation"
	role={$mobile && $showSidebar ? 'dialog' : undefined}
	aria-modal={$mobile && $showSidebar ? 'true' : undefined}
	aria-label={$mobile && $showSidebar ? 'Navigation' : undefined}
	class="h-screen max-h-[100dvh] min-h-0 select-none {$showSidebar
		? 'md:relative w-[260px] max-w-[260px]'
		: '-translate-x-[260px] w-[0px]'} {$isApp
		? `ml-[4.5rem] md:ml-0 `
		: 'transition-width duration-200 ease-in-out'}  shrink-0 bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-200 text-sm fixed z-50 top-0 left-0 overflow-x-hidden
        "
	data-state={sidebarStateMarker($showSidebar)}
>
	<div
		class="py-2 my-auto flex flex-col justify-between h-full min-h-0 max-h-full w-full overflow-x-hidden z-50 {$showSidebar
			? ''
			: 'invisible'}"
		aria-hidden={!$showSidebar}
		inert={!$showSidebar}
	>
		<PinnedMenuItems />
		<div class="px-1.5 flex justify-between space-x-1 text-gray-600 dark:text-gray-400">
			<button
				bind:this={closeNavigationButton}
				class=" cursor-pointer p-[7px] flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-900 transition"
				aria-label={$i18n.t('Close navigation')}
				on:click={() => {
					showSidebar.set(!$showSidebar);
				}}
			>
				<div class=" m-auto self-center">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						class="size-5"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12"
						/>
					</svg>
				</div>
			</button>

			<a
				id="sidebar-new-chat-button"
				class="flex justify-between items-center flex-1 rounded-lg px-2 py-1 h-full text-right hover:bg-gray-100 dark:hover:bg-gray-900 transition no-drag-region"
				href="/"
				draggable="false"
				on:click={async () => {
					selectedChatId = null;
					await goto('/');
					const newChatButton = document.getElementById('new-chat-button');
					setTimeout(() => {
						newChatButton?.click();
						if ($mobile) {
							showSidebar.set(false);
						}
					}, 0);
				}}
			>
				<div id="sidebar-branding-row" class="flex min-w-0 items-center gap-1 whitespace-nowrap">
					<div class="mx-1.5 flex shrink-0 items-center gap-1 self-center whitespace-nowrap">
						<img
							crossorigin="anonymous"
							src="{WEBUI_BASE_URL}/static/favicon.png"
							class=" size-5 -translate-x-1.5 rounded-full"
							alt="OpenAtelierUI"
						/>
					</div>
					<div id="sidebar-webui-name" class="self-center font-medium text-sm text-gray-850 dark:text-white font-primary">
						{$WEBUI_NAME}
					</div>
				</div>

				<div>
					<PencilSquare className=" size-5" strokeWidth="2" />
				</div>
			</a>
		</div>

        <a id="sidebar-new-chat-row" href="/" class="mx-2 flex items-center gap-2 rounded-lg px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-900"
            on:click={() => { chatId.set(''); selectedChatId = null; if ($mobile) showSidebar.set(false); }}><PencilSquare className="size-4" />{$i18n.t('New Chat')}</a>

		<!-- {#if $user?.role === 'admin'}
			<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200">
				<a
					class="grow flex items-center space-x-3 rounded-lg px-2 py-[7px] hover:bg-gray-100 dark:hover:bg-gray-900 transition"
					href="/home"
					on:click={() => {
						selectedChatId = null;
						chatId.set('');

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
					draggable="false"
				>
					<div class="self-center">
						<Home strokeWidth="2" className="size-[1.1rem]" />
					</div>

					<div class="flex self-center translate-y-[0.5px]">
						<div class=" self-center font-medium text-sm font-primary">{$i18n.t('Home')}</div>
					</div>
				</a>
			</div>
		{/if} -->

		{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
			<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200">
				<a
					class="grow flex items-center space-x-3 rounded-lg px-2 py-[7px] hover:bg-gray-100 dark:hover:bg-gray-900 transition"
					href="/workspace"
					on:click={() => {
						selectedChatId = null;
						chatId.set('');

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
					draggable="false"
				>
					<div class="self-center">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							class="size-[1.1rem]"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
							/>
						</svg>
					</div>

					<div class="flex self-center translate-y-[0.5px]">
						<div class=" self-center font-medium text-sm font-primary">{$i18n.t('Workspace')}</div>
					</div>
				</a>
			</div>
		{/if}
		{#if $config?.features?.openatelier_private_notes}
			<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200">
				<a class="grow flex items-center space-x-3 rounded-lg px-2 py-[7px] hover:bg-gray-100 dark:hover:bg-gray-900 transition" href="/notes" on:click={() => { selectedChatId = null; chatId.set(''); if ($mobile) showSidebar.set(false); }} draggable="false">
					<Document className="size-[1.1rem]" />
					<span class="self-center font-medium text-sm font-primary">Notes</span>
				</a>
			</div>
		{/if}

		<div class="relative {$temporaryChatEnabled ? 'opacity-20' : ''}">
			{#if $temporaryChatEnabled}
				<div class="absolute z-40 w-full h-full flex justify-center"></div>
			{/if}

			<SearchInput
				bind:value={search}
				on:input={searchDebounceHandler}
				placeholder={$i18n.t('Search')}
				showClearButton={true}
			/>
		</div>

		<div
			class="relative flex flex-col flex-1 overflow-y-auto overflow-x-hidden {$temporaryChatEnabled
				? 'opacity-20'
				: ''}"
		>
			{#if $config?.features?.enable_channels && ($user?.role === 'admin' || $channels.length > 0) && !search}
				<Folder
					className="px-2 mt-0.5"
					name={$i18n.t('Channels')}
					dragAndDrop={false}
					onAdd={async () => {
						if ($user?.role === 'admin') {
							await tick();

							setTimeout(() => {
								showCreateChannel = true;
							}, 0);
						}
					}}
					onAddLabel={$i18n.t('Create Channel')}
				>
					{#each $channels as channel}
						<ChannelItem
							{channel}
							onUpdate={async () => {
								await initChannels();
							}}
						/>
					{/each}
				</Folder>
			{/if}

			{#if !search && folders}
				<Folder
					id="folders-section"
					className="px-2 mt-0.5"
					name={$i18n.t('Folders')}
					onAdd={() => createFolder()}
					onAddLabel={$i18n.t('New Folder')}
				>
					<Folders
						{folders}
						on:import={(e) => {
							const { folderId, items } = e.detail;
							importChatHandler(items, false, folderId);
						}}
						on:update={async () => {
							initChatList();
						}}
						on:change={async () => {
							initChatList();
						}}
					/>
				</Folder>
			{/if}

			<Folder
				collapsible={false}
				className="px-2 mt-0.5"
				name={$i18n.t('Chats')}
				on:import={(e) => {
					importChatHandler(e.detail);
				}}
				on:drop={async (e) => {
					const { type, id, item } = e.detail;

					if (type === 'chat') {
						let chat = await getChatById(localStorage.token, id).catch((error) => {
							return null;
						});
						if (!chat && item) {
							chat = await importChat(localStorage.token, item.chat, item?.meta ?? {});
						}

						if (chat) {
							console.log(chat);
							if (chat.folder_id) {
								const res = await updateChatFolderIdById(localStorage.token, chat.id, null).catch(
									(error) => {
										toast.error(`${error}`);
										return null;
									}
								);
							}

							if (chat.pinned) {
								const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
							}

							initChatList();
						}
					} else if (type === 'folder') {
						if (folders[id].parent_id === null) {
							return;
						}

						const res = await updateFolderParentIdById(localStorage.token, id, null).catch(
							(error) => {
								toast.error(`${error}`);
								return null;
							}
						);

						if (res) {
							await initFolders();
						}
					}
				}}
			>
				{#if $temporaryChatEnabled}
					<div class="absolute z-40 w-full h-full flex justify-center"></div>
				{/if}

				{#if !search && $pinnedChats.length > 0}
					<div class="flex flex-col space-y-1 rounded-xl">
						<Folder
							className=""
							bind:open={showPinnedChat}
							on:change={(e) => {
								localStorage.setItem('showPinnedChat', e.detail);
								console.log(e.detail);
							}}
							on:import={(e) => {
								importChatHandler(e.detail, true);
							}}
							on:drop={async (e) => {
								const { type, id, item } = e.detail;

								if (type === 'chat') {
									let chat = await getChatById(localStorage.token, id).catch((error) => {
										return null;
									});
									if (!chat && item) {
										chat = await importChat(localStorage.token, item.chat, item?.meta ?? {});
									}

									if (chat) {
										console.log(chat);
										if (chat.folder_id) {
											const res = await updateChatFolderIdById(
												localStorage.token,
												chat.id,
												null
											).catch((error) => {
												toast.error(`${error}`);
												return null;
											});
										}

										if (!chat.pinned) {
											const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
										}

										initChatList();
									}
								}
							}}
							name={$i18n.t('Pinned')}
						>
							<div
								id="pinned-chat-items-list"
								class="ml-3 pl-1 mt-[1px] flex flex-col overflow-y-auto scrollbar-hidden border-s border-gray-100 dark:border-gray-900"
							>
								{#each $pinnedChats as chat, idx}
									<ChatItem
										className=""
										id={chat.id}
										title={chat.title}
										updatedAt={chat.updated_at}
										{shiftKey}
										selected={selectedChatId === chat.id}
										on:select={() => {
											selectedChatId = chat.id;
										}}
										on:unselect={() => {
											selectedChatId = null;
										}}
										on:change={async () => {
											initChatList();
										}}
										on:tag={(e) => {
											const { type, name } = e.detail;
											tagEventHandler(type, name, chat.id);
										}}
									/>
								{/each}
							</div>
						</Folder>
					</div>
				{/if}

				<div class=" flex-1 flex flex-col overflow-y-auto scrollbar-hidden">
					<div class="pt-1.5">
						{#if $chats}
							{#each $chats as chat, idx}
								{#if idx === 0 || (idx > 0 && chat.time_range !== $chats[idx - 1].time_range)}
									<div
										class="w-full pl-2.5 text-xs text-gray-500 dark:text-gray-500 font-medium {idx ===
										0
											? ''
											: 'pt-5'} pb-1.5"
									>
										{$i18n.t(chat.time_range)}
										<!-- localisation keys for time_range to be recognized from the i18next parser (so they don't get automatically removed):
							{$i18n.t('Today')}
							{$i18n.t('Yesterday')}
							{$i18n.t('Previous 7 days')}
							{$i18n.t('Previous 30 days')}
							{$i18n.t('January')}
							{$i18n.t('February')}
							{$i18n.t('March')}
							{$i18n.t('April')}
							{$i18n.t('May')}
							{$i18n.t('June')}
							{$i18n.t('July')}
							{$i18n.t('August')}
							{$i18n.t('September')}
							{$i18n.t('October')}
							{$i18n.t('November')}
							{$i18n.t('December')}
							-->
									</div>
								{/if}

								<ChatItem
									className=""
									id={chat.id}
									title={chat.title}
										updatedAt={chat.updated_at}
									{shiftKey}
									selected={selectedChatId === chat.id}
									on:select={() => {
										selectedChatId = chat.id;
									}}
									on:unselect={() => {
										selectedChatId = null;
									}}
									on:change={async () => {
										initChatList();
									}}
									on:tag={(e) => {
										const { type, name } = e.detail;
										tagEventHandler(type, name, chat.id);
									}}
								/>
							{/each}

							{#if $showSidebar && $scrollPaginationEnabled && !allChatsLoaded && ($chats?.length ?? 0) > 0}
								<Loader
									on:visible={(e) => {
										if (!chatListLoading) {
											loadMoreChats();
										}
									}}
								>
									<div
										class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
									>
										<Spinner className=" size-4" />
										<div class=" ">Loading...</div>
									</div>
								</Loader>
							{/if}
						{:else}
							<div class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2">
								<Spinner className=" size-4" />
								<div class=" ">Loading...</div>
							</div>
						{/if}
					</div>
				</div>
			</Folder>
		</div>

		<div class="px-2">
			<div class="flex flex-col font-primary">
				{#if $user !== undefined && $user !== null}
					<UserMenu
						role={$user?.role}
						let:builder
						on:show={(e) => {
							if (e.detail === 'archived-chat') {
								showArchivedChats.set(true);
							}
						}}
					>
						<button
							{...builder}
							use:builder.action
							aria-haspopup="menu"
							class=" flex items-center rounded-xl py-2.5 px-2.5 w-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
							on:click={() => {
								showDropdown = !showDropdown;
							}}
						>
							<div class=" self-center mr-3">
								<img
									src={$user?.profile_image_url}
									class=" max-w-[30px] object-cover rounded-full"
									alt="User profile"
								/>
							</div>
							<div class=" self-center font-medium">{$user?.name}</div>
						</button>
					</UserMenu>
				{/if}
			</div>
		</div>
	</div>
</div>

    <div id="sidebar-resizer" role="separator" tabindex="0" aria-label={$i18n.t('Resize sidebar')} aria-orientation="vertical"
        aria-valuemin="200" aria-valuemax="360" aria-valuenow={sidebarWidth}
        class="hidden md:block absolute top-0 right-0 z-[60] h-full w-1 cursor-col-resize touch-none focus:bg-blue-500"
        on:pointerdown={(event) => { resizing = true; event.currentTarget.setPointerCapture(event.pointerId); }}
        on:pointermove={(event) => { if (resizing) setSidebarWidth(event.clientX); }}
        on:pointerup={() => resizing = false} on:pointercancel={() => resizing = false}
        on:keydown={(event) => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
            event.preventDefault(); setSidebarWidth(event.key === 'Home' ? 200 : event.key === 'End' ? 360 : sidebarWidth + (event.key === 'ArrowRight' ? 10 : -10));
        } }}></div>

{/if}
</div>
{#if latestCompatibility && !$showSidebar && !$mobile}
	<CurrentSidebarShadow webuiName={$WEBUI_NAME} />
{/if}
{/if}
{/if}

<style>
	.scrollbar-hidden:active::-webkit-scrollbar-thumb,
	.scrollbar-hidden:focus::-webkit-scrollbar-thumb,
	.scrollbar-hidden:hover::-webkit-scrollbar-thumb {
		visibility: visible;
	}
	.scrollbar-hidden::-webkit-scrollbar-thumb {
		visibility: hidden;
	}
</style>

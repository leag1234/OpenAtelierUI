<script lang="ts">
	import { config, user } from '$lib/stores';

	export let webuiName = 'OpenAtelierUI';
</script>

<div
	role="navigation"
	aria-label="Chat history"
	aria-hidden="true"
	inert
	style:width="0px"
	class="h-screen max-h-[100dvh] min-h-screen select-none bg-gray-50 dark:bg-gray-950 z-50 pointer-events-none shrink-0 text-gray-700 dark:text-gray-300 text-[0.8125rem] leading-5 fixed top-0 left-0 overflow-x-hidden"
>
	<div class="my-auto flex flex-col justify-between h-screen max-h-[100dvh] w-[var(--sidebar-width)] overflow-x-hidden scrollbar-hidden z-50 border-e border-gray-50 dark:border-gray-850/30">
		<div class="sidebar px-1 pt-0.5 pb-0.5 flex shrink-0 justify-between space-x-1 text-gray-600 dark:text-gray-400 sticky top-0 z-10">
			<a href="/" tabindex="-1" class="flex items-center rounded-xl size-8.5 shrink-0 justify-center hover:bg-gray-100 dark:hover:bg-gray-900 transition no-drag-region">
				<img class="sidebar-new-chat-icon size-5 rounded-full" alt="" src="/static/favicon.png" />
			</a>
			<a href="/" tabindex="-1" class="flex min-w-0 flex-1 px-0.5">
				<div id="sidebar-webui-name" class="self-center truncate font-normal text-gray-700 dark:text-gray-200">{webuiName}</div>
			</a>
			<div class="flex shrink-0 items-center gap-px">
				<div class="flex">
					<button type="button" tabindex="-1" class="flex size-[1.875rem] shrink-0 items-center justify-center rounded-lg text-gray-600 dark:text-gray-400 transition hover:bg-gray-100 dark:hover:bg-gray-900">
						<span class="relative flex size-4 items-center justify-center">
							<svg aria-hidden="true" viewBox="0 0 24 24" class="size-4"><path d="M12 3v18M3 12h18" /></svg>
						</span>
					</button>
				</div>
				<div class="flex">
					<button type="button" tabindex="-1" class="flex size-[1.875rem] justify-center items-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-900 transition cursor-[w-resize]">
						<div class="self-center">
							<svg aria-hidden="true" viewBox="0 0 24 24" class="size-4">
								<path d="M4 4h16v16H4z" />
								<path d="M9 4v16" />
								<path d="m6 10 2 2-2 2" />
							</svg>
						</div>
					</button>
				</div>
			</div>
			<div class="invisible sidebar-bg-gradient-to-b bg-linear-to-b from-gray-50 dark:from-gray-950 to-transparent from-50% pointer-events-none absolute inset-0 -z-10 -mb-6"></div>
		</div>

		<section id="sidebar-notifications" class="flex min-h-0 flex-1 flex-col pt-2.5">
			<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-1.5 pb-3 flex flex-col">
				<div class="flex flex-1 items-center justify-center px-4 py-6 text-gray-500 dark:text-gray-400">
					<div class="flex justify-center text-center">
						<svg aria-hidden="true" viewBox="0 0 24 24" class="size-4">
							<path d="M12 3a9 9 0 0 1 9 9" />
							<path class="spinner_ajPY" d="M21 12a9 9 0 0 1-9 9" />
						</svg>
					</div>
				</div>
			</div>
		</section>

		<div class="relative flex flex-col flex-1 overflow-y-auto scrollbar-hidden space-y-1.5 pb-2.5">
			<div class="pb-1">
				<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
					<a id="sidebar-new-chat-button" href="/" tabindex="-1" class="group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none">
						<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-4"><path d="M12 20h9" /></svg></div>
						<div class="flex flex-1 self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">New Chat</div></div>
						<span class="hidden md:inline-flex items-center self-center hover-reveal"><span class="inline-flex min-h-[1.125rem] shrink-0 items-center justify-center rounded-full bg-gray-100 px-[0.3125rem] py-0.5 text-[0.625rem] font-medium leading-none text-gray-500 dark:bg-white/6 dark:text-gray-400">N</span></span>
					</a>
				</div>
				<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
					<button id="sidebar-search-button" type="button" tabindex="-1" class="group grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition outline-none">
						<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-4"><path d="m17 17 4 4" /><path d="M3 11a8 8 0 1 1 16 0 8 8 0 0 1-16 0" /></svg></div>
						<div class="flex flex-1 self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">Search</div></div>
						<span class="hidden md:inline-flex items-center self-center hover-reveal"><span class="inline-flex min-h-[1.125rem] shrink-0 items-center justify-center rounded-full bg-gray-100 px-[0.3125rem] py-0.5 text-[0.625rem] font-medium leading-none text-gray-500 dark:bg-white/6 dark:text-gray-400">S</span></span>
					</button>
				</div>

				<div id="pinned-menu-items-list">
					{#if $config?.features?.openatelier_private_notes}
						<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
							<a id="sidebar-notes-button" href="/notes" tabindex="-1" class="grow flex items-center space-x-2 rounded-xl px-2 py-1.5 transition hover:bg-gray-100 dark:hover:bg-gray-900">
								<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-4"><path d="M5 3h14v18H5z" /><path d="M8 7h8" /><path d="M8 11h8" /><path d="M8 15h5" /><path d="M8 19h4" /></svg></div>
								<div class="flex self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">Notes</div></div>
							</a>
						</div>
					{/if}
					{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
						<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
							<a id="sidebar-workspace-button" href="/workspace" tabindex="-1" class="grow flex items-center space-x-2 rounded-xl px-2 py-1.5 transition hover:bg-gray-100 dark:hover:bg-gray-900">
								<div class="self-center flex size-4 shrink-0 items-center justify-center"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-4"><path d="M4 4h6v6H4z" /><path d="M14 4h6v6h-6z" /><path d="M4 14h6v6H4z" /><path d="M17 14v6" /></svg></div>
								<div class="flex self-center translate-y-[0.5px]"><div class="self-center text-[0.8125rem] leading-5">Workspace</div></div>
							</a>
						</div>
					{/if}
				</div>
			</div>

			<div class="relative">
				<div class="w-full"><div class="w-full cursor-pointer"><div><div class="flex items-start justify-between"><div class="flex items-center justify-between h-6 w-full pl-3.5 pr-1.5 shrink-0"><h2 class="flex flex-1 h-full"><button id="sidebar-folders-button" type="button" tabindex="-1" class="group flex flex-1 h-full items-center gap-1 text-left text-xs text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400 transition-colors duration-100"><span>Folders</span><span class="flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-all duration-100"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-[0.6875rem]"><path d="m9 18 6-6-6-6" /></svg></span></button></h2><div class="contents"></div><button type="button" tabindex="-1" class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"><div class="flex"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-3.5"><path d="M12 5v14m-7-7h14" /></svg></div></button></div></div></div></div></div>
			</div>

			<div class="relative">
				<div class="w-full">
					<div class="w-full cursor-pointer"><div><div class="flex items-start justify-between"><div class="flex items-center justify-between h-6 w-full pl-3.5 pr-1.5 shrink-0"><h2 class="flex flex-1 h-full"><button id="sidebar-chats-button" type="button" tabindex="-1" class="group flex flex-1 h-full items-center gap-1 text-left text-xs text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400 transition-colors duration-100"><span>Chats</span><span class="flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-all duration-100"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-[0.6875rem]"><path d="m9 18 6-6-6-6" /></svg></span></button></h2><div class="contents"><span><div class="flex"><button type="button" tabindex="-1" class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"><svg aria-hidden="true" viewBox="0 0 24 24" class="size-3.5"><path d="M12 5v14" /><path d="M5 12h14" /><path d="M7 7h10" /></svg></button></div></span></div></div></div></div></div>
					<div><div id="sidebar-chats-content" class="w-full px-1.5"><div class="flex-1 flex flex-col overflow-y-auto scrollbar-hidden"><div class="pt-1.5"><div role="list"></div></div></div></div></div>
				</div>
			</div>
		</div>

		<div class="px-1 pt-1 pb-1.5 sticky bottom-0 z-10 -mt-2 sidebar">
			<div class="sidebar-bg-gradient-to-t bg-linear-to-t from-gray-50 dark:from-gray-950 to-transparent from-50% pointer-events-none absolute inset-0 -z-10 -mt-6"></div>
			<div class="flex flex-col"><span><button type="button" tabindex="-1" class="flex items-center rounded-xl py-1.5 px-1.5 w-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"><div class="self-center mr-3 relative flex-shrink-0"><img src={$user?.profile_image_url ?? '/static/favicon.png'} alt="" class="size-5.5 object-cover rounded-full" /><div class="absolute -bottom-0.5 -right-0.5"><span class="relative flex size-2.5"><span class="relative inline-flex size-2.5 rounded-full bg-green-500 border-2 border-white dark:border-gray-900"></span></span></div></div><div class="self-center font-normal truncate">{$user?.name ?? 'User'}</div></button></span></div>
		</div>
	</div>
</div>

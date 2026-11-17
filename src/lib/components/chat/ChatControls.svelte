<script lang="ts">
	import { SvelteFlowProvider } from '@xyflow/svelte';
	import { slide } from 'svelte/transition';
	import { Pane, PaneResizer } from 'paneforge';

	import { onDestroy, onMount, tick } from 'svelte';
	import {
		config,
		mobile,
		showControls,
		showCallOverlay,
		showOverview,
		showArtifacts,
		temporaryChatEnabled
	} from '$lib/stores';

	import Modal from '../common/Modal.svelte';
	import Controls from './Controls/Controls.svelte';
	import CallOverlay from './MessageInput/CallOverlay.svelte';
	import Drawer from '../common/Drawer.svelte';
	import Overview from './Overview.svelte';
	import EllipsisVertical from '../icons/EllipsisVertical.svelte';
	import Artifacts from './Artifacts.svelte';
	import { min } from '@floating-ui/utils';

	export let history;
	export let models = [];
	export let title = 'New Chat';

	export let chatId = null;

	export let chatFiles = [];
	export let params = {};

	export let eventTarget: EventTarget;
	export let submitPrompt: Function;
	export let stopResponse: Function;
	export let showMessage: Function;
	export let files;
	export let modelId;

	export let pane;

	let mediaQuery;
	let largeScreen = false;
	let dragged = false;

	let minSize = 0;

	export const openPane = () => {
		if (parseInt(localStorage?.chatControlsSize)) {
			pane.resize(parseInt(localStorage?.chatControlsSize));
		} else {
			pane.resize(minSize);
		}
	};

	const handleMediaQuery = async (e) => {
		if (e.matches) {
			largeScreen = true;

			if ($showCallOverlay) {
				showCallOverlay.set(false);
				await tick();
				showCallOverlay.set(true);
			}
		} else {
			largeScreen = false;

			if ($showCallOverlay) {
				showCallOverlay.set(false);
				await tick();
				showCallOverlay.set(true);
			}
			pane = null;
		}
	};

	const onMouseDown = (event) => {
		dragged = true;
	};

	const onMouseUp = (event) => {
		dragged = false;
	};

	onMount(() => {
		// listen to resize 1024px
		mediaQuery = window.matchMedia('(min-width: 1024px)');

		mediaQuery.addEventListener('change', handleMediaQuery);
		handleMediaQuery(mediaQuery);

		// Select the container element you want to observe
		const container = document.getElementById('chat-container');

		// initialize the minSize based on the container width
		minSize = Math.floor((350 / container.clientWidth) * 100);

		// Create a new ResizeObserver instance
		const resizeObserver = new ResizeObserver((entries) => {
			for (let entry of entries) {
				const width = entry.contentRect.width;
				// calculate the percentage of 200px
				const percentage = (350 / width) * 100;
				// set the minSize to the percentage, must be an integer
				minSize = Math.floor(percentage);

				if ($showControls) {
					if (pane && pane.isExpanded() && pane.getSize() < minSize) {
						pane.resize(minSize);
					}
				}
			}
		});

		// Start observing the container's size changes
		resizeObserver.observe(container);

		document.addEventListener('mousedown', onMouseDown);
		document.addEventListener('mouseup', onMouseUp);
	});

	onDestroy(() => {
		showControls.set(false);

		mediaQuery.removeEventListener('change', handleMediaQuery);
		document.removeEventListener('mousedown', onMouseDown);
		document.removeEventListener('mouseup', onMouseUp);
	});

	const closeHandler = () => {
		showControls.set(false);
		showOverview.set(false);
		showArtifacts.set(false);

		if ($showCallOverlay) {
			showCallOverlay.set(false);
		}
	};

	$: if (!chatId) {
		closeHandler();
	}
	$: latestCompatibility = $config?.compatibility_profile === 'native';
</script>

{#if latestCompatibility && $mobile}
	<div role="presentation" id="controls-container" class="h-full w-full min-h-0 flex flex-col hidden" style="--chat-tabs-dark-background: var(--color-gray-900);">
		<div class="relative flex-1 min-h-0 min-w-0">
			<div role="presentation" class="absolute min-h-0 min-w-0 pointer-events-none flex flex-col overflow-hidden" data-panel-group="default">
				<div data-panel-tab-bar="" class="flex h-9 items-center px-1.5 gap-1 shrink-0 pointer-events-auto select-none col-start-1 row-start-1 min-w-0 z-10">
					<div class="flex items-center gap-0.5 flex-1 min-w-0 py-0.5">
						<div role="tablist" tabindex="-1" data-swipe-no-gesture class="panel-tab-strip flex items-center gap-0.5 min-w-0 overflow-x-auto" aria-label="Tabs">
							<div data-panel-tab="main-chat" class="panel-tab flex cursor-pointer items-center gap-1.5 ps-2.5 pe-2 h-7 rounded-lg text-sm font-normal whitespace-nowrap shrink-0 transition bg-gray-100/40 dark:bg-gray-800/25 text-gray-700 dark:text-gray-200">
								<button role="tab" class="flex h-full items-center gap-1.5 min-w-0" id="chat-panel-main-chat" aria-controls="chat-panel-main-chat-view" aria-selected="true" tabindex="0" {title} aria-label={title}>
									<span class="max-w-30 overflow-hidden text-ellipsis">{title}</span>
								</button>
							</div>
						</div>
						<span style="display: contents; cursor: pointer;"><button type="button" class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0" aria-label="New tab" aria-haspopup="true" aria-expanded="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" width="14" height="14" stroke-width="1.5" class="shrink-0"><path d="M6 12H12M18 12H12M12 12V6M12 12V18"></path></svg></button></span>
					</div>
					<button type="button" class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0" aria-label="Hide tabs"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" width="12" height="12" stroke-width="1.5" class="shrink-0"><path d="M6.75827 17.2426L12.0009 12M17.2435 6.75736L12.0009 12M12.0009 12L6.75827 6.75736M12.0009 12L17.2435 17.2426"></path></svg></button>
				</div>
			</div>
		</div>
	</div>
{:else if latestCompatibility}
	<div class="contents">
		<div
			role="presentation"
			id="controls-container"
			class="h-full w-full min-h-0 flex flex-col absolute inset-0 pointer-events-none"
			style="--chat-tabs-dark-background: var(--color-gray-900); left: 42px; width: calc(100% - 42px);"
		>
			<div class="relative flex-1 min-h-0 min-w-0">
				<button
					type="button"
					class="absolute right-2 top-1 z-20 flex size-7 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-50/40 dark:text-gray-400 dark:hover:bg-gray-800/40 disabled:opacity-30 pointer-events-auto"
					aria-label="Split screen"
					aria-pressed="false"
				>
					<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" class="size-4.5" stroke-width="1">
						<rect x="4" y="4" width="16" height="16" rx="1.5"></rect>
						<path d="M12 20V4"></path>
					</svg>
				</button>
				<div
					role="presentation"
					class="absolute min-h-0 min-w-0 pointer-events-none flex flex-col overflow-hidden"
					data-panel-group="default"
					style="position: absolute; left: 0%; top: calc(100% * 0 + 0px * var(--app-text-scale, 1)); width: 100%; height: calc(100% * 1 - 0px * var(--app-text-scale, 1));"
				>
					<div
						data-panel-tab-bar=""
						class="flex h-9 items-center px-1.5 pr-10 gap-1 shrink-0 pointer-events-auto select-none col-start-1 row-start-1 min-w-0 z-10"
					>
						<div class="flex items-center gap-0.5 flex-1 min-w-0 py-0.5">
							<div role="tablist" aria-label="Tabs" tabindex="-1" data-swipe-no-gesture class="panel-tab-strip flex items-center gap-0.5 min-w-0 overflow-x-auto">
								<div data-panel-tab="main-chat" class="panel-tab flex cursor-pointer items-center gap-1.5 ps-2.5 pe-2 h-7 rounded-lg text-sm font-normal whitespace-nowrap shrink-0 transition bg-gray-100/40 dark:bg-gray-800/25 text-gray-700 dark:text-gray-200">
									<button role="tab" class="flex h-full items-center gap-1.5 min-w-0" id="chat-panel-main-chat" aria-controls="chat-panel-main-chat-view" aria-selected="true" tabindex="0" {title} aria-label={title}>
										<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" width="14" height="14" stroke-width="1.5" class="shrink-0">
											<path d="M8 10L12 10L16 10"></path>
											<path d="M8 14L10 14L12 14"></path>
											<path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 13.8214 2.48697 15.5291 3.33782 17L2.5 21.5L7 20.6622C8.47087 21.513 10.1786 22 12 22Z"></path>
										</svg>
										<span class="max-w-30 overflow-hidden text-ellipsis">{title}</span>
									</button>
								</div>
							</div>
							<span style="display: contents; cursor: pointer;">
								<button type="button" class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0" aria-label="New tab" aria-haspopup="true" aria-expanded="false">
									<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" width="14" height="14" stroke-width="1.5" class="shrink-0">
										<path d="M6 12H12M18 12H12M12 12V6M12 12V18"></path>
									</svg>
								</button>
							</span>
						</div>
						<div class="flex shrink-0 items-center gap-2">
							<div class="flex">
								<button type="button" class="flex size-6 cursor-pointer items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800/40 dark:hover:text-gray-200" aria-label="Tabs" aria-expanded="false">
									<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" class="size-4" stroke-width="1"><rect x="4" y="4" width="16" height="16" rx="1.5"></rect><path d="M12 20V4"></path></svg>
								</button>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	</div>
{/if}

<SvelteFlowProvider>
	{#if !largeScreen}
		{#if $showControls}
			<Drawer
				show={$showControls}
				on:close={() => {
					showControls.set(false);
				}}
			>
				<div
					class=" {$showCallOverlay || $showOverview || $showArtifacts
						? ' h-screen  w-full'
						: 'px-6 py-4'} h-full"
				>
					{#if $showCallOverlay}
						<div
							class=" h-full max-h-[100dvh] bg-white text-gray-700 dark:bg-black dark:text-gray-300 flex justify-center"
						>
							<CallOverlay
								bind:files
								{submitPrompt}
								{stopResponse}
								{modelId}
								{chatId}
								{eventTarget}
								on:close={() => {
									showControls.set(false);
								}}
							/>
						</div>
					{:else if $showArtifacts}
						<Artifacts {history} />
					{:else if $showOverview}
						<Overview
							{history}
							on:nodeclick={(e) => {
								showMessage(e.detail.node.data.message);
							}}
							on:close={() => {
								showControls.set(false);
							}}
						/>
					{:else}
						<Controls
							on:close={() => {
								showControls.set(false);
							}}
							{models}
							bind:chatFiles
							bind:params
						/>
					{/if}
				</div>
			</Drawer>
		{/if}
	{:else}
		<!-- if $showControls -->

		{#if $showControls}
			<PaneResizer class="relative flex w-2 items-center justify-center bg-background group">
				<div class="z-10 flex h-7 w-5 items-center justify-center rounded-xs">
					<EllipsisVertical className="size-4 invisible group-hover:visible" />
				</div>
			</PaneResizer>
		{/if}

		<Pane
			bind:pane
			defaultSize={0}
			onResize={(size) => {
				console.log('size', size, minSize);

				if ($showControls && pane.isExpanded()) {
					if (size < minSize) {
						pane.resize(minSize);
					}

					if (size < minSize) {
						localStorage.chatControlsSize = 0;
					} else {
						localStorage.chatControlsSize = size;
					}
				}
			}}
			onCollapse={() => {
				showControls.set(false);
			}}
			collapsible={true}
			class={latestCompatibility ? '' : ' z-10 '}
		>
			{#if $showControls}
				<div class="flex max-h-full min-h-full">
					<div
						class="w-full {($showOverview || $showArtifacts) && !$showCallOverlay
							? ' '
							: 'px-4 py-4 bg-white dark:shadow-lg dark:bg-gray-850  border border-gray-100 dark:border-gray-850'} z-40 pointer-events-auto overflow-y-auto scrollbar-hidden"
					>
						{#if $showCallOverlay}
							<div class="w-full h-full flex justify-center">
								<CallOverlay
									bind:files
									{submitPrompt}
									{stopResponse}
									{modelId}
									{chatId}
									{eventTarget}
									on:close={() => {
										showControls.set(false);
									}}
								/>
							</div>
						{:else if $showArtifacts}
							<Artifacts {history} overlay={dragged} />
						{:else if $showOverview}
							<Overview
								{history}
								on:nodeclick={(e) => {
									if (e.detail.node.data.message.favorite) {
										history.messages[e.detail.node.data.message.id].favorite = true;
									} else {
										history.messages[e.detail.node.data.message.id].favorite = null;
									}

									showMessage(e.detail.node.data.message);
								}}
								on:close={() => {
									showControls.set(false);
								}}
							/>
						{:else}
							<Controls
								on:close={() => {
									showControls.set(false);
								}}
								{models}
								bind:chatFiles
								bind:params
							/>
						{/if}
					</div>
				</div>
			{/if}
		</Pane>
	{/if}
</SvelteFlowProvider>

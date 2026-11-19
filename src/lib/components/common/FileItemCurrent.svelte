<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { formatFileSize } from '$lib/utils';

	import FileItemModal from './FileItemModal.svelte';
	import Spinner from './Spinner.svelte';

	const dispatch = createEventDispatcher();

	export let url: string | null = null;
	export let dismissible = false;
	export let loading = false;
	export let item = null;
	export let edit = false;
	export let name: string;
	export let type: string;
	export let size: number;

	let showModal = false;

	const decodeString = (value: string) => {
		try {
			return decodeURIComponent(value);
		} catch {
			return value;
		}
	};

	const openItem = async () => {
		if (item?.file?.data?.content) {
			showModal = !showModal;
		} else if (url) {
			const target = type === 'file' ? `${url}/content` : url;
			window.open(target, '_blank')?.focus();
		}

		dispatch('click');
	};
</script>

{#if item}
	<FileItemModal bind:show={showModal} bind:item {edit} />
{/if}

<div class="relative group w-60">
	<button
		type="button"
		class="flex w-full items-center bg-white dark:bg-gray-850 border border-gray-50/30 dark:border-gray-800/30 h-8 gap-1.5 rounded-xl px-2.5 text-[0.8125rem] leading-5 text-left"
		on:click={openItem}
	>
		<div class="shrink-0 text-gray-500 dark:text-gray-400">
			<div class="flex">
				{#if loading}
					<Spinner className="size-3.5" />
				{:else}
					<svg
						aria-hidden="true"
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="1.5"
						class="size-3.5"
					>
						<path d="M5 2h10l4 4v16H5Z" stroke-linecap="round" stroke-linejoin="round" />
						<path d="M15 2v4h4" stroke-linecap="round" stroke-linejoin="round" />
						<path d="M8 10h8" stroke-linecap="round" stroke-linejoin="round" />
						<path d="M8 14h8" stroke-linecap="round" stroke-linejoin="round" />
						<path d="M8 18h4" stroke-linecap="round" stroke-linejoin="round" />
					</svg>
				{/if}
			</div>
		</div>

		<div class="flex min-w-0 flex-1 overflow-hidden">
			<div class="flex min-w-0 flex-1 items-center overflow-hidden">
				<div class="flex min-w-0 flex-1 items-center justify-between dark:text-gray-100">
					<div class="min-w-0 flex-1 truncate pr-1 font-normal">{decodeString(name)}</div>
					<div class="max-w-[35%] shrink-0 truncate text-[0.6875rem] capitalize text-gray-500">
						{size !== undefined && size !== null ? formatFileSize(size) : type}
					</div>
				</div>
			</div>
		</div>
	</button>

	{#if dismissible}
		<div class="absolute -top-[3px] -right-[3px] flex">
			<button
				type="button"
				aria-label="Remove File"
				class="bg-white text-black border border-gray-50 rounded-full hover-reveal transition"
				on:click|stopPropagation={() => dispatch('dismiss')}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					aria-hidden="true"
					stroke-width="2"
					class="size-4"
				>
					<path d="M6 6l8 8m0-8-8 8" />
				</svg>
			</button>
		</div>
	{/if}
</div>

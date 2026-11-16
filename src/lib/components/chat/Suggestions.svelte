<script lang="ts">
	import Fuse from 'fuse.js';
	import Bolt from '$lib/components/icons/Bolt.svelte';
	import { onMount, getContext, createEventDispatcher } from 'svelte';
	import { WEBUI_NAME, config } from '$lib/stores';
	import { WEBUI_VERSION } from '$lib/constants';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let suggestionPrompts = [];
	export let className = '';
	export let inputValue = '';

	let sortedPrompts = [];

	const fuseOptions = {
		keys: ['content', 'title'],
		threshold: 0.5
	};

	let fuse;
	let filteredPrompts = [];
	$: v0113Compatibility = $config?.compatibility_profile !== 'native';
	const localized = (value) => ($i18n.exists(value) ? $i18n.t(value) : value);

	// Initialize Fuse
	$: fuse = new Fuse(sortedPrompts, fuseOptions);

	// Update the filteredPrompts if inputValue changes
	// Only increase version if something wirklich geändert hat
	$: getFilteredPrompts(inputValue);

	// Helper function to check if arrays are the same
	// (based on unique IDs oder content)
	function arraysEqual(a, b) {
		if (a.length !== b.length) return false;
		for (let i = 0; i < a.length; i++) {
			if ((a[i].id ?? a[i].content) !== (b[i].id ?? b[i].content)) {
				return false;
			}
		}
		return true;
	}

	const getFilteredPrompts = (inputValue) => {
		if (inputValue.length > 500) {
			filteredPrompts = [];
		} else {
			const newFilteredPrompts =
				inputValue.trim() && fuse
					? fuse.search(inputValue.trim()).map((result) => result.item)
					: sortedPrompts;

			// Compare with the oldFilteredPrompts
			// If there's a difference, update array + version
			if (!arraysEqual(filteredPrompts, newFilteredPrompts)) {
				filteredPrompts = newFilteredPrompts;
			}
		}
	};

	$: if (suggestionPrompts) {
		sortedPrompts = [...(suggestionPrompts ?? [])].sort(() => Math.random() - 0.5);
		getFilteredPrompts(inputValue);
	}
</script>

<div class={v0113Compatibility
	? 'mb-1 flex gap-1 text-xs font-medium items-center text-gray-400 dark:text-gray-600'
	: 'mb-1 flex gap-1 text-xs font-normal items-center text-gray-600 dark:text-gray-400'}>
	{#if filteredPrompts.length > 0}
		<Bolt ariaHidden={!v0113Compatibility} />
		{#if v0113Compatibility}
			{$i18n.t('Suggested')}
		{:else}
			<h3 id="suggestions-heading">{$i18n.t('Suggested')}</h3>
		{/if}
	{:else}
		<!-- Keine Vorschläge -->

		<div
			class="flex w-full text-center items-center justify-center self-start text-gray-400 dark:text-gray-600"
		>
			{$WEBUI_NAME} ‧ v{WEBUI_VERSION}
		</div>
	{/if}
</div>

{#if v0113Compatibility}
	<div class={`h-40 overflow-auto scrollbar-none ${className} items-start`}>
		{#if filteredPrompts.length > 0}
			{#each filteredPrompts as prompt, idx (prompt.id || prompt.content)}
				<button
					class="waterfall flex flex-col flex-1 shrink-0 w-full justify-between px-3 py-2 rounded-xl bg-transparent hover:bg-black/5 dark:hover:bg-white/5 transition group"
					style="animation-delay: {idx * 60}ms"
					on:click={() => dispatch('select', localized(prompt.content))}
				>
					<div class="flex flex-col text-left">
						{#if prompt.title && prompt.title[0] !== ''}
							<div class="font-medium dark:text-gray-300 dark:group-hover:text-gray-200 transition line-clamp-1">
								{localized(prompt.title[0])}
							</div>
							<div class="text-xs text-gray-500 font-normal line-clamp-1">
								{localized(prompt.title[1])}
							</div>
						{:else}
							<div class="font-medium dark:text-gray-300 dark:group-hover:text-gray-200 transition line-clamp-1">
								{localized(prompt.content)}
							</div>
							<div class="text-xs text-gray-500 font-normal line-clamp-1">{$i18n.t('Prompt')}</div>
						{/if}
					</div>
				</button>
			{/each}
		{/if}
	</div>
{:else}
	<div class="h-36 w-full">
		<div role="list" aria-labelledby="suggestions-heading" class="max-h-36 overflow-auto scrollbar-none items-start {className}">
			{#if filteredPrompts.length > 0}
				{#each filteredPrompts.slice(0, 6) as prompt, idx (prompt.id || prompt.content)}
					<div role="listitem" class="contents">
						<button
							class="waterfall flex flex-col flex-1 shrink-0 w-full justify-between px-2.5 py-1.5 rounded-lg bg-transparent transition-colors hover:text-gray-950 dark:hover:text-white group"
							style="animation-delay: {idx * 60}ms"
							on:click={() => dispatch('select', localized(prompt.content))}
						>
							<div class="flex flex-col text-left leading-snug">
								{#if prompt.title && prompt.title[0] !== ''}
									<div class="text-sm font-normal group-hover:text-gray-950 dark:text-gray-300 dark:group-hover:text-white transition line-clamp-1">
										{localized(prompt.title[0])}
									</div>
									<div class="text-xs text-gray-600 group-hover:text-gray-900 dark:text-gray-400 dark:group-hover:text-gray-100 font-normal line-clamp-1">
										{localized(prompt.title[1])}
									</div>
								{:else}
									<div class="text-sm font-normal group-hover:text-gray-950 dark:text-gray-300 dark:group-hover:text-white transition line-clamp-1">
										{localized(prompt.content)}
									</div>
									<div class="text-xs text-gray-600 group-hover:text-gray-900 dark:text-gray-400 dark:group-hover:text-gray-100 font-normal line-clamp-1">{$i18n.t('Prompt')}</div>
								{/if}
							</div>
						</button>
					</div>
				{/each}
			{/if}
		</div>
	</div>
{/if}

<style>
	/* Waterfall animation for the suggestions */
	@keyframes fadeInUp {
		0% {
			opacity: 0;
			transform: translateY(20px);
		}
		100% {
			opacity: 1;
			transform: translateY(0);
		}
	}

	.waterfall {
		opacity: 0;
		animation-name: fadeInUp;
		animation-duration: 200ms;
		animation-fill-mode: forwards;
		animation-timing-function: ease;
	}
</style>

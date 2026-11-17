<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { statusDescription, statusSearchQuery, statusSearchUrls } from '$lib/utils/chatStatus.js';
	import WebSearchResults from './ResponseMessage/WebSearchResults.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let status;
</script>

<div class="text-[0.90625rem] flex flex-col w-full">
	<div class="relative w-full">
		<button
			class="absolute inset-0"
			aria-expanded="false"
			aria-label={$i18n.t(statusDescription(status))}
		></button>
		<div class="relative pointer-events-none flex items-start gap-2">
			<div class="status-description flex items-center gap-2 py-0.5 w-full text-left">
				{#if status?.action === 'web_search' && statusSearchUrls(status)}
					<WebSearchResults {status}>
						<div class="flex flex-col justify-center -space-y-0.5">
							<div
								class="{status?.done === false ? 'shimmer' : ''} text-[0.90625rem] line-clamp-1 text-wrap"
							>
								{#if statusDescription(status).includes('{{count}}')}
									{$i18n.t(statusDescription(status), {
										count: statusSearchUrls(status).length
									})}
								{:else if statusDescription(status) === 'No search query generated'}
									{$i18n.t('No search query generated')}
								{:else if statusDescription(status) === 'Generating search query'}
									{$i18n.t('Generating search query')}
								{:else}
									{statusDescription(status)}
								{/if}
							</div>
						</div>
					</WebSearchResults>
				{:else if status?.action === 'knowledge_search' && statusSearchQuery(status)}
					<div class="flex flex-col justify-center -space-y-0.5">
						<div
							class="{status?.done === false
								? 'shimmer'
								: ''} text-gray-500 dark:text-gray-500 text-[0.90625rem] line-clamp-1 text-wrap"
						>
							{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
								searchQuery: statusSearchQuery(status)
							})}
						</div>
					</div>
				{:else}
					<div class="flex flex-col justify-center -space-y-0.5">
						<div
							class="{status?.done === false
								? 'shimmer'
								: ''} text-gray-500 dark:text-gray-500 text-[0.90625rem] text-wrap line-clamp-1"
						>
							{#if statusDescription(status).includes('{{searchQuery}}')}
								{$i18n.t(statusDescription(status), {
									searchQuery: statusSearchQuery(status) ?? ''
								})}
							{:else if statusDescription(status) === 'No search query generated'}
								{$i18n.t('No search query generated')}
							{:else if statusDescription(status) === 'Generating search query'}
								{$i18n.t('Generating search query')}
							{:else}
								{statusDescription(status)}
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

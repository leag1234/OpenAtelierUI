<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';

	const dispatch = createEventDispatcher();
	export let id = '';

	let toolbar: HTMLDivElement;
	let visible = false;
	let selectedText = '';
	let left = 0;
	let top = 0;

	const portalToBody = (node: HTMLElement) => {
		document.body.appendChild(node);

		return {
			destroy() {
				node.remove();
			}
		};
	};

	const updateSelection = () => {
		const selection = window.getSelection();
		const text = selection?.toString().trim() ?? '';
		const anchor = selection?.anchorNode;
		const element = anchor instanceof Element ? anchor : anchor?.parentElement;

		if (!selection || selection.rangeCount === 0 || !text || !element?.closest('.chat-assistant')) {
			visible = false;
			return;
		}

		const bounds = selection.getRangeAt(0).getBoundingClientRect();
		selectedText = text;
		left = Math.max(8, Math.min(window.innerWidth - (toolbar?.offsetWidth ?? 180) - 8, bounds.left));
		top = Math.max(8, bounds.top - (toolbar?.offsetHeight ?? 34) - 8);
		visible = true;
	};

	const useSelection = (action: 'Ask' | 'Explain') => {
		dispatch('prompt', { action, text: selectedText });
		visible = false;
	};

	onMount(() => document.addEventListener('selectionchange', updateSelection));
	onDestroy(() => document.removeEventListener('selectionchange', updateSelection));
</script>

<div
	bind:this={toolbar}
	id={`floating-buttons-${id}-${id}`}
	use:portalToBody
	class="fixed max-w-[calc(100vw-1rem)] rounded-lg text-xs z-9999"
	style:display={visible ? 'block' : 'none'}
	style:left={`${left}px`}
	style:top={`${top}px`}
>
	<div class="flex flex-row flex-wrap shrink-0 p-0.5 bg-white dark:bg-gray-850 dark:text-gray-100 text-medium rounded-xl shadow-xl border border-gray-100 dark:border-gray-800">
		<button
			class="px-1.5 py-[0.0625rem] min-h-9 sm:min-h-0 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl flex items-center gap-1 min-w-fit transition"
			aria-label="Ask"
		on:mousedown|preventDefault={() => {}}
			on:click={() => useSelection('Ask')}
		>
			<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" class="size-3 shrink-0">
				<path stroke-linecap="round" stroke-linejoin="round" d="M8.625 12h-.375m4.125 0H12m4.125 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a4.48 4.48 0 0 0 .504-2.09C4.12 17.34 3 14.91 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
			</svg>
			<div class="shrink-0">Ask</div>
		</button>
		<button
			class="px-1.5 py-[0.0625rem] min-h-9 sm:min-h-0 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl flex items-center gap-1 min-w-fit transition"
			aria-label="Explain"
		on:mousedown|preventDefault={() => {}}
			on:click={() => useSelection('Explain')}
		>
			<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" class="size-3 shrink-0">
				<path stroke-linecap="round" stroke-linejoin="round" d="M12 18v-5.25m2.25 7.289a12.06 12.06 0 0 1-4.5 0M14.25 18v-.192c0-.983.658-1.823 1.508-2.316a7.5 7.5 0 1 0-7.517 0c.85.493 1.509 1.333 1.509 2.316V18" />
			</svg>
			<div class="shrink-0">Explain</div>
		</button>
		</div>
	</div>

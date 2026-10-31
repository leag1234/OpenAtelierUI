<script lang="ts">
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import MenuLines from '$lib/components/icons/MenuLines.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import { WEBUI_NAME, showSidebar } from '$lib/stores';

	let selected = '';
</script>

<div class="min-w-[4.5rem] bg-gray-50 dark:bg-gray-950 flex gap-2.5 flex-col pt-8">
	<div class="flex justify-center relative">
		{#if selected === 'home'}
			<div class="absolute top-0 left-0 flex h-full">
				<div class="my-auto rounded-r-lg w-1 h-8 bg-black dark:bg-white"></div>
			</div>
		{/if}

		<Tooltip content="Home" placement="right">
			<button
				class=" cursor-pointer {selected === 'home' ? 'rounded-2xl' : 'rounded-full'}"
				aria-label="Home"
				on:click={() => {
					selected = 'home';

					if (window.electronAPI) {
						window.electronAPI.load('home');
					}
				}}
			>
				{#if $WEBUI_NAME === 'OpenAtelierUI'}
					<img
						src="/static/favicon.svg"
						class="size-11 p-0.5 dark:hidden"
						alt=""
						aria-hidden="true"
						draggable="false"
					/>
					<img
						src="/static/openatelierui-symbol-dark.svg"
						class="hidden size-11 p-0.5 dark:block"
						alt=""
						aria-hidden="true"
						draggable="false"
					/>
				{:else}
					<img
						src="/static/splash.png"
						class="size-11 dark:invert p-0.5"
						alt=""
						aria-hidden="true"
						draggable="false"
					/>
				{/if}
			</button>
		</Tooltip>
	</div>

	{#if !$showSidebar}
		<div class="flex justify-center relative">
			<Tooltip content="Open navigation" placement="right">
				<button
					class="cursor-pointer rounded-full p-2 hover:bg-gray-100 dark:hover:bg-gray-900 transition"
					aria-label="Open navigation"
					aria-controls="sidebar"
					aria-expanded={$showSidebar}
					on:click={() => showSidebar.set(true)}
				>
					<MenuLines />
				</button>
			</Tooltip>
		</div>
	{/if}

	<div class=" -mt-1 border-[1.5px] border-gray-100 dark:border-gray-900 mx-4"></div>

	<div class="flex justify-center relative group">
		{#if selected === ''}
			<div class="absolute top-0 left-0 flex h-full">
				<div class="my-auto rounded-r-lg w-1 h-8 bg-black dark:bg-white"></div>
			</div>
		{/if}
		<button
			class=" cursor-pointer bg-transparent"
			on:click={() => {
				selected = '';
			}}
		>
			<img
				src="/static/favicon.png"
				class="size-10 {selected === '' ? 'rounded-2xl' : 'rounded-full'}"
				alt="OpenAtelierUI"
				draggable="false"
			/>
		</button>
	</div>

	<!-- <div class="flex justify-center relative group text-gray-400">
		<button class=" cursor-pointer p-2" on:click={() => {}}>
			<Plus className="size-4" strokeWidth="2" />
		</button>
	</div> -->
</div>

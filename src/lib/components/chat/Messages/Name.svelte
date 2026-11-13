<script lang="ts">
	export let current = false;
</script>

{#if current}
	<h3 class="self-center text-[0.90625rem] font-normal line-clamp-1 flex gap-1 items-center">
		<slot />
	</h3>
{:else}
	<div class=" self-center font-semibold line-clamp-1 flex gap-1 items-center">
		<slot />
	</div>
{/if}

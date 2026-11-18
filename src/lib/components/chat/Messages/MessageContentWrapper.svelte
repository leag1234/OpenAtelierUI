<script lang="ts">
	export let wrapped = false;
</script>

{#if wrapped}
	<div class="w-full"><slot /></div>
{:else}
	<slot />
{/if}

<script lang="ts">
	export let wrapped = false;
</script>

{#if wrapped}
	<div class="flex"><slot /></div>
{:else}
	<slot />
{/if}

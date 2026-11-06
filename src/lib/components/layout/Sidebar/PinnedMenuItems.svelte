<script lang="ts">
	export let compact = false;
</script>

<!-- Stable integration mount; only the active navigation mode renders it. -->
<div id="pinned-menu-items-list" class="flex flex-col gap-1 {compact ? 'items-center' : ''}"></div>

<script lang="ts">
	import { WEBUI_BASE_URL } from '$lib/constants';

	export let className = 'size-8';
	export let src = `${WEBUI_BASE_URL}/static/favicon.png`;
	export let currentCompatibility = false;
</script>

<img
	crossorigin={currentCompatibility ? undefined : 'anonymous'}
	aria-hidden={currentCompatibility ? 'true' : undefined}
	src={src === ''
		? `${WEBUI_BASE_URL}/static/favicon.png`
		: src.startsWith(WEBUI_BASE_URL) ||
			  src.startsWith('https://www.gravatar.com/avatar/') ||
			  src.startsWith('data:') ||
			  src.startsWith('/')
			? src
			: `/user.png`}
	class=" {className} object-cover {currentCompatibility ? 'rounded-2xl' : 'rounded-full -translate-y-[1px]'}"
	alt="profile"
	draggable="false"
/>

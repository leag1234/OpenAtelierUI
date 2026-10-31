<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME } from '$lib/stores';
	import ArrowRightCircle from './icons/ArrowRightCircle.svelte';

	export let show = true;
	export let getStartedHandler = () => {};

	function setLogoImage() {
		const logo = document.getElementById('logo');

		if (logo) {
			const isDarkMode = document.documentElement.classList.contains('dark');

			if (isDarkMode) {
				const darkImage = new Image();
				darkImage.src = '/static/favicon-dark.png';

				darkImage.onload = () => {
					logo.src = '/static/favicon-dark.png';
					logo.style.filter = ''; // Ensure no inversion is applied if splash-dark.png exists
				};

				darkImage.onerror = () => {
					logo.style.filter = 'invert(1)'; // Invert image if splash-dark.png is missing
				};
			}
		}
	}

	$: if (show) {
		setLogoImage();
	}
</script>

{#if show}
	<div class="w-full h-screen max-h-[100dvh] text-white relative">
		<div class="fixed m-10 z-50">
			<div class="flex space-x-2">
				<div class=" self-center">
					<img
						id="logo"
						crossorigin="anonymous"
						src="{WEBUI_BASE_URL}/static/favicon.png"
						class=" w-6 rounded-full"
						alt="OpenAtelierUI"
					/>
				</div>
			</div>
		</div>

		<div class="absolute inset-0 bg-linear-to-t from-20% from-black to-transparent"></div>
		<div class="absolute inset-0 backdrop-blur-xs bg-black/50"></div>

		<main
			class="relative z-10 flex min-h-screen w-full items-center justify-center px-6 text-center"
			role="dialog"
			aria-modal="true"
			aria-labelledby="first-use-heading"
		>
			<div class="max-w-2xl">
				<h1 id="first-use-heading" class="font-secondary text-4xl font-semibold sm:text-6xl">
					{$i18n.t('Welcome to {{WEBUI_NAME}}', { WEBUI_NAME: $WEBUI_NAME })}
				</h1>
				<p class="mx-auto mt-5 max-w-xl text-base text-white/85 sm:text-lg">
					{$i18n.t('Set up your account to work with conversations, files, and models on this server.')}
				</p>
				<button
					type="button"
					class="relative z-20 mt-8 inline-flex min-h-12 items-center justify-center gap-3 rounded-full bg-white/10 px-6 py-3 font-medium text-white transition hover:bg-white/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
					on:click={getStartedHandler}
				>
					<span>{$i18n.t('Continue to account setup')}</span>
					<ArrowRightCircle className="size-6" />
				</button>
			</div>
		</main>
	</div>
{/if}

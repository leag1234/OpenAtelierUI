<script>
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { getToolById, getTools, updateToolById, updateToolAccessById } from '$lib/apis/tools';
	import { buildReadGrantsFromParsed, parseResourceReadGrants, saveResourceWithSharing } from '$lib/apis/models/sharing.js';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToolkitEditor from '$lib/components/workspace/Tools/ToolkitEditor.svelte';
	import { WEBUI_VERSION } from '$lib/constants';
	import { tools } from '$lib/stores';
	import { compareVersion, extractFrontmatter } from '$lib/utils';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	let tool = null;

	const saveHandler = async (data) => {
		console.log(data);

		const manifest = extractFrontmatter(data.content);
		if (compareVersion(manifest?.required_open_webui_version ?? '0.0.0', WEBUI_VERSION)) {
			console.log('Version is lower than required');
			toast.error(
				$i18n.t(
					'OpenAtelierUI version (v{{OPEN_WEBUI_VERSION}}) is lower than required version (v{{REQUIRED_VERSION}})',
					{
						OPEN_WEBUI_VERSION: WEBUI_VERSION,
						REQUIRED_VERSION: manifest?.required_open_webui_version ?? '0.0.0'
					}
				)
			);
			return;
		}

		const parsed = parseResourceReadGrants(tool, 'tool', true);
		const existingGrants = parsed.supported
			? buildReadGrantsFromParsed(parsed)
			: undefined;
		const result = await saveResourceWithSharing(data.id, data, existingGrants,
			(id, details) => updateToolById(localStorage.token, id, details),
			(id, grants) => updateToolAccessById(localStorage.token, id, grants)
		);
		if (result.status === 'sharing_failed') {
			toast.error('Tool details were saved, but sharing was not updated. Retry the sharing change.');
			return;
		}
		if (result.status !== 'saved') {
			toast.error('The tool details could not be saved. Review the form and try again.');
			return;
		}

		if (result.model) {
			tool = result.model;
			toast.success($i18n.t('Tool updated successfully'));
			tools.set(await getTools(localStorage.token));

			// await goto('/workspace/tools');
		}
	};

	onMount(async () => {
		console.log('mounted');
		const id = $page.url.searchParams.get('id');

		if (id) {
			tool = await getToolById(localStorage.token, id).catch((error) => {
				toast.error(`${error}`);
				goto('/workspace/tools');
				return null;
			});

			console.log(tool);
		}
	});
</script>

{#if tool}
	<ToolkitEditor
		edit={true}
		id={tool.id}
		name={tool.name}
		meta={tool.meta}
		content={tool.content}
		existingTool={tool}
		onSave={(value) => {
			return saveHandler(value);
		}}
	/>
{:else}
	<div class="flex items-center justify-center h-full">
		<div class=" pb-16">
			<Spinner />
		</div>
	</div>
{/if}

<script>
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { page } from '$app/stores';
	import { config, models, settings } from '$lib/stores';

	import { getModelById, updateModelById, updateModelAccessById } from '$lib/apis/models';
	import { buildModelReadGrants, parseModelReadGrants, saveModelWithSharing } from '$lib/apis/models/sharing.js';

	import { getModels } from '$lib/apis';
	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';

	let model = null;

	onMount(async () => {
		const _id = $page.url.searchParams.get('id');
		if (_id) {
			model = await getModelById(localStorage.token, _id).catch((e) => {
				return null;
			});

			if (!model) {
				goto('/workspace/models');
			}
		} else {
			goto('/workspace/models');
		}
	});

	const onSubmit = async (modelInfo) => {
		const parsed = parseModelReadGrants(model);
		const existingGrants = parsed.supported
			? buildModelReadGrants(parsed.visibility, parsed.userIds.join('\n'))
			: undefined;
		const result = await saveModelWithSharing(
			modelInfo.id,
			modelInfo,
			existingGrants,
			(id, details) => updateModelById(localStorage.token, id, details),
			(id, grants) => updateModelAccessById(localStorage.token, id, grants)
		);

		if (result.status === 'sharing_failed') {
			toast.error('Model details were saved, but sharing was not updated. Retry the sharing change.');
			return;
		}
		if (result.status !== 'saved') {
			toast.error('The model details could not be saved. Review the form and try again.');
			return;
		}

		if (result.model) {
			await models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
		}
	};
</script>

{#if model}
	<ModelEditor edit={true} {model} {onSubmit} />
{/if}

<script lang="ts">
	import { getContext } from 'svelte';

	import { toast } from 'svelte-sonner';
	import { deleteSharedChatById, getChatById, shareChatById } from '$lib/apis/chats';
	import { copyToClipboard } from '$lib/utils';
	import { getChatGrants, setChatGrants, searchRecipients } from '$lib/apis/chats/sharing';
	import { getUserById } from '$lib/apis/users';

	import Modal from '../common/Modal.svelte';
	import Link from '../icons/Link.svelte';

	export let chatId;

	let chat = null;
	let shareUrl = null;
 let recipients = [];
 let retainedGrants = [];
 let search = '';
 let results = [];
 let busy = false;
 let error = '';
 let accessChanged = false;
 let loadEpoch = 0;
 let accessLoaded = false;
 let searched = false;
 let panelEpoch = 0;

 async function loadAccess(id) {
  const epoch = ++loadEpoch;
  busy = true; accessLoaded = false; searched = false; error = ''; search = ''; results = []; recipients = []; retainedGrants = [];
  try {
   const grants = await getChatGrants(localStorage.token, id);
   const readers = [];
   for (const grant of grants) {
    if (grant.principal_type === 'user' && grant.permission === 'read') {
     let name = $i18n.t('Unavailable account');
     try { name = (await getUserById(localStorage.token, grant.principal_id)).name; } catch {}
     readers.push({ id: grant.principal_id, name });
    }
   }
   if (epoch === loadEpoch) {
    recipients = readers;
    retainedGrants = grants.filter((g) => g.principal_type !== 'user' || g.permission !== 'read');
    accessChanged = false; accessLoaded = true;
   }
  } catch { if (epoch === loadEpoch) error = $i18n.t('Sharing permissions could not be loaded. Close and reopen this panel to retry.'); }
  finally { if (epoch === loadEpoch) busy = false; }
 }

 async function findPeople() {
  if (busy || !accessLoaded) return;
  busy = true; error = '';
  try { results = (await searchRecipients(localStorage.token, search)).users; searched = true; }
  catch { error = $i18n.t('People could not be loaded. Try searching again.'); }
  finally { busy = false; }
 }

 async function saveAccess() {
  const grants = [...retainedGrants, ...recipients.map((person) => ({ principal_type: 'user', principal_id: person.id, permission: 'read' }))];
  await setChatGrants(localStorage.token, chatId, grants);
  accessChanged = false;
 }

	const i18n = getContext('i18n');

	const shareLocalChat = async () => {
		const id = chatId;

		const sharedChat = await shareChatById(localStorage.token, id);
		shareUrl = `${window.location.origin}/s/${sharedChat.share_id ?? sharedChat.id}`;

		chat = await getChatById(localStorage.token, id);

		return shareUrl;
	};

	export let show = false;

	const isDifferentChat = (_chat) => {
		if (!chat) {
			return true;
		}
		if (!_chat) {
			return false;
		}
		return chat.id !== _chat.id || chat.share_id !== _chat.share_id;
	};

 async function loadPanel(id) {
  const epoch = ++panelEpoch;
  busy = true; accessLoaded = false; error = '';
  try {
   const current = await getChatById(localStorage.token, id);
   if (epoch !== panelEpoch || !show || id !== chatId) return;
   chat = current; shareUrl = current.share_id ? `${window.location.origin}/s/${current.share_id}` : null;
   await loadAccess(id);
  } catch { error = $i18n.t('Sharing permissions could not be loaded. Close and reopen this panel to retry.'); }
  finally { if (epoch === panelEpoch) busy = false; }
 }
 $: if (show && chatId) loadPanel(chatId);

</script>

<Modal bind:show size="md" trapFocus={true} labelledBy="share-chat-heading">
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-5 pt-4 pb-0.5">
			<div id="share-chat-heading" class=" text-lg font-medium self-center">{$i18n.t('Share Chat')}</div>
			<button
				class="self-center"
                aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="w-5 h-5"
				>
					<path
						d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
					/>
				</svg>
			</button>
		</div>

		{#if chat}
			<div class="px-5 pt-4 pb-5 w-full flex flex-col justify-center">
				<div class=" text-sm dark:text-gray-300 mb-1">
                    {#if chat.share_id}
                        <p>{$i18n.t('Only invited readers and authorized administrators can open this link.')}</p>
                        <p class="mt-2">{$i18n.t('Deleting the link also removes all invitations.')}
                            <button type="button" disabled={busy || !accessLoaded} class="underline" on:click={async () => {
                                const id = chatId;
                                busy = true; error = '';
                                try {
                                    await deleteSharedChatById(localStorage.token, id);
                                    chat = await getChatById(localStorage.token, id);
                                    shareUrl = null;
                                    await loadAccess(id);
                                } catch { error = $i18n.t('Sharing could not be updated. Your changes have not been saved.'); }
                                finally { busy = false; }
                            }}>{$i18n.t('Delete link and invitations')}</button>
                        </p>
					{:else}
						{$i18n.t(
							"Creating a link does not grant access. Only the owner, authorized administrators, and invited readers can open this conversation."
						)}
					{/if}
				</div>

                <section class="mt-4 space-y-3" aria-label={$i18n.t('People with access')}>
                    <h3 class="text-sm font-semibold">{$i18n.t('People with access')}</h3>
                    <form class="flex gap-2" on:submit|preventDefault={findPeople}>
                        <input class="min-w-0 flex-1 rounded-lg border p-2 dark:bg-gray-850" maxlength="256" readonly={busy || !accessLoaded} on:input={() => { searched = false; results = []; }} bind:value={search} aria-label={$i18n.t('Find a person by name or email')} placeholder={$i18n.t('Find a person by name or email')} />
                        <button type="submit" disabled={busy || !accessLoaded} class="rounded-lg bg-gray-100 px-3 dark:bg-gray-800">{$i18n.t('Search')}</button>
                    </form>
                    {#if results.length}
                        <ul class="max-h-40 overflow-auto rounded-lg border">
                            {#each results as person (person.id)}
                                <li><button type="button" disabled={recipients.some((item) => item.id === person.id)} class="w-full p-2 text-left hover:bg-gray-100 dark:hover:bg-gray-800" on:click={() => { recipients = [...recipients, person]; accessChanged = true; results = []; searched = false; }}><span class="block truncate">{person.name}</span><span class="block truncate text-xs text-gray-500">{person.email}</span></button></li>
                            {/each}
                        </ul>
                    {/if}
                    {#if searched && !results.length}<p class="text-sm text-gray-500">{$i18n.t('No results found')}</p>{/if}
                    {#if accessChanged}<p role="status" class="text-sm text-amber-700 dark:text-amber-300">{$i18n.t('Unsaved changes. Save access to apply them. Closing discards these changes.')}</p>{/if}
                    <ul class="max-h-40 overflow-auto">
                        {#each recipients as person (person.id)}
                            <li class="flex items-center justify-between gap-3 py-1"><span class="truncate text-sm">{person.name}</span><button type="button" disabled={busy} class="rounded px-2 py-1 text-sm underline" aria-label={$i18n.t('Remove access for {{name}}', {name:person.name})} on:click={() => { recipients = recipients.filter((item) => item.id !== person.id); accessChanged = true; }}>{$i18n.t('Remove')}</button></li>
                        {/each}
                    </ul>
                    {#if retainedGrants.length}<p class="text-xs text-gray-500">{$i18n.t('Other existing access permissions will be kept.')}</p>{/if}
                    {#if accessChanged}<button type="button" disabled={busy || !accessLoaded} class="rounded-lg bg-gray-100 px-3 py-2 text-sm dark:bg-gray-800" on:click={async () => { busy = true; error = ''; try { await saveAccess(); toast.success($i18n.t('Access updated')); } catch { error = $i18n.t('Sharing could not be updated. Your changes have not been saved.'); } finally { busy = false; } }}>{$i18n.t('Save access')}</button>{/if}
                    {#if error}<p role="alert" class="text-sm text-red-600 dark:text-red-400">{error}</p>{/if}
                    {#if shareUrl}<a class="block break-all text-sm underline" href={shareUrl}>{shareUrl}</a>{/if}
                </section>

				<div class="flex justify-end">
					<div class="flex flex-col items-end space-x-1 mt-3">
						<div class="flex gap-1">
							<button
								class="self-center flex items-center gap-1 px-3.5 py-2 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
								type="button"
								id="copy-and-share-chat-button"
                                disabled={busy || !accessLoaded || accessChanged}
                                on:click={async () => {
                                    busy = true; error = '';
                                    try {
                                        const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
                                        if (isSafari) {
                                            const promisedText = shareLocalChat().then((url) => new Blob([url], {type:'text/plain'}));
                                            await navigator.clipboard.write([new ClipboardItem({'text/plain':promisedText})]);
                                        } else {
                                            const copied = await copyToClipboard(await shareLocalChat());
                                            if (!copied) throw new Error($i18n.t('Copy failed. You can open or select the link below.'));
                                        }
                                        toast.success($i18n.t('Copied shared chat URL to clipboard!'));
                                        show = false;
                                    } catch { error = $i18n.t('Copy failed. You can open or select the link below.'); }
                                    finally { busy = false; }
                                }}
							>
								<Link />

								{#if chat.share_id}
									{$i18n.t('Update and Copy Link')}
								{:else}
									{$i18n.t('Copy Link')}
								{/if}
							</button>
						</div>
					</div>
				</div>
			</div>
        {:else}
            <p class="px-5 py-4" role={error ? 'alert' : 'status'}>{error || $i18n.t('Loading...')}</p>
		{/if}
	</div>
</Modal>

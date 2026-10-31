#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-extracted checks for temporary chat identity and owner-scoped stop."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
CHAT = BASE / "src/lib/components/chat/Chat.svelte"


def extract_function(source: str, name: str) -> str:
    marker = f"const {name} = "
    start = source.index(marker)
    body_start = source.index("=> {", start) + 3
    depth = 0
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1] + ";"
    raise AssertionError(f"could not extract {name}")


class TemporaryChatUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = CHAT.read_text(encoding="utf-8")
        cls.init_chat = extract_function(source, "initChatHandler").replace(
            "$settings.system ?? undefined", "$settings.system || undefined"
        )
        cls.stop_response = extract_function(source, "stopResponse")
        cls.stop_response = cls.stop_response.replace(
            "responseAtStop?.parentId", "responseAtStop && responseAtStop.parentId"
        ).replace(
            "parentAtStop?.childrenIds ?? [history.currentId]",
            "(parentAtStop && parentAtStop.childrenIds) || [history.currentId]",
        ).replace(
            "history.messages[id]?.done", "(history.messages[id] && history.messages[id].done)"
        ).replace("result?.status", "(result && result.status)")
        cls.source = source

    def _run_node(self, body: str) -> None:
        result = subprocess.run(["node", "-e", body], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_temporary_initializer_creates_unique_local_ids_and_reuses_current_conversation(self):
        prelude = r"""
const assert = require('assert');
let $chatId = '';
const writes = [];
const chatId = {set: async value => { $chatId = value; writes.push(value); }};
const chatTitle = {set: async () => {}};
const chats = {set: async () => {}};
const $temporaryChatEnabled = true;
const tick = async () => {};
const uuidValues = ['first-uuid', 'second-uuid'];
const uuidv4 = () => uuidValues.shift();
const selectedModels = [];
const createNewChat = async () => { throw new Error('temporary chat must not persist'); };
"""
        assertions = r"""
(async () => {
  assert.strictEqual(await initChatHandler({}), 'local:first-uuid');
  assert.strictEqual(await initChatHandler({}), 'local:first-uuid');
  await chatId.set(''); // initNewChat clears the store before the next conversation.
  assert.strictEqual(await initChatHandler({}), 'local:second-uuid');
  assert.deepStrictEqual(writes, ['local:first-uuid', 'local:first-uuid', '', 'local:second-uuid']);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        self._run_node(prelude + self.init_chat + assertions)

    def test_temporary_stop_uses_owner_context_and_never_persists_history(self):
        script = self.stop_response + r"""
(async () => {
  await stopResponse();
  assert.deepStrictEqual(calls, ['stop:local:temp-1']);
  assert.strictEqual(history.messages.reply.done, true);
  assert.strictEqual(persistCalls, 0);
  assert.strictEqual(taskIds, null);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        prelude = r"""
const assert = require('assert');
let $chatId = 'local:temp-1';
const $temporaryChatEnabled = true;
let history = {currentId:'reply', messages:{reply:{parentId:'user',done:false}, user:{childrenIds:['reply']}}};
let taskIds = ['task-1'];
const calls = [];
let persistCalls = 0;
const localStorage = {token:'synthetic'};
const normalizeTaskIds = value => value && Array.isArray(value.task_ids) ? value.task_ids : Array.isArray(value) ? value : [];
const chatResultMatchesCurrent = (target,current) => target === current;
const markCurrentResponsesDone = (confirmed, ids) => { if (confirmed) history.messages.reply.done = true; return confirmed; };
const persistStoppedChat = async () => { persistCalls += 1; };
const toast = {error: message => { throw new Error(`unexpected toast: ${message}`); }};
const requestSavedChatStop = async (stop, list, current) => {
  const response = await stop();
  if (!current()) return {state:'navigated'};
  let tasks = null;
  if (response.status === false) tasks = await list();
  if (!current()) return {state:'navigated'};
  if (response.status === true) return {state:'stopped'};
  if (tasks && tasks.task_ids && tasks.task_ids.length === 0) return {state:'already-finished'};
  return {state:'uncertain',taskListResponse:tasks};
};
const stopTasksByChatId = async (_token,id) => { calls.push(`stop:${id}`); return {status:true}; };
const getTaskIdsByChatId = async (_token,id) => { calls.push(`list:${id}`); return {task_ids:[]}; };
const stopTask = async (_token,id) => { calls.push(`direct:${id}`); return {status:true}; };
"""
        self._run_node(prelude + script)

    def test_temporary_stop_failure_does_not_mark_or_save_and_saved_guard_remains(self):
        script = self.stop_response + r"""
(async () => {
  await stopResponse();
  assert.deepStrictEqual(calls, ['stop:local:temp-2', 'list:local:temp-2']);
  assert.strictEqual(history.messages.reply.done, false);
  assert.strictEqual(persistCalls, 0);
  assert.deepStrictEqual(notices, ['The response may still be running. Try stopping it again.']);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        prelude = r"""
const assert = require('assert');
let $chatId = 'local:temp-2';
const $temporaryChatEnabled = true;
let history = {currentId:'reply', messages:{reply:{parentId:'user',done:false}, user:{childrenIds:['reply']}}};
let taskIds = ['task-2'];
const calls = [];
let persistCalls = 0;
const notices = [];
const localStorage = {token:'synthetic'};
const normalizeTaskIds = value => value && Array.isArray(value.task_ids) ? value.task_ids : Array.isArray(value) ? value : [];
const chatResultMatchesCurrent = (target,current) => target === current;
const markCurrentResponsesDone = confirmed => { if (confirmed) history.messages.reply.done = true; return confirmed; };
const persistStoppedChat = async () => { persistCalls += 1; };
const toast = {error: message => notices.push(message)};
const requestSavedChatStop = async (stop,list,current) => {
  const response = await stop();
  if (!current()) return {state:'navigated'};
  const tasks = response.status === false ? await list() : null;
  if (!current()) return {state:'navigated'};
  return response.status === true ? {state:'stopped'} : {state:'uncertain',taskListResponse:tasks};
};
const stopTasksByChatId = async (_token,id) => { calls.push(`stop:${id}`); return {status:false}; };
const getTaskIdsByChatId = async (_token,id) => { calls.push(`list:${id}`); return {task_ids:['task-2']}; };
const stopTask = async (_token,id) => { calls.push(`direct:${id}`); return {status:true}; };
"""
        self._run_node(prelude + script)

    def test_saved_chat_stop_keeps_existing_persistence_behavior(self):
        prelude = r"""
const assert = require('assert');
let $chatId = 'saved-chat';
const $temporaryChatEnabled = false;
let history = {currentId:'reply', messages:{reply:{parentId:'user',done:false}, user:{childrenIds:['reply']}}};
let taskIds = ['task-saved'];
let persistCalls = 0;
const localStorage = {token:'synthetic'};
const normalizeTaskIds = value => value && Array.isArray(value.task_ids) ? value.task_ids : Array.isArray(value) ? value : [];
const chatResultMatchesCurrent = (target,current) => target === current;
const isEphemeralChatId = value => value === 'local' || (typeof value === 'string' && value.startsWith('local:'));
const markCurrentResponsesDone = confirmed => { if (confirmed) history.messages.reply.done = true; return confirmed; };
const persistStoppedChat = async id => { assert.strictEqual(id, 'saved-chat'); persistCalls += 1; };
const toast = {error: message => { throw new Error(`unexpected toast: ${message}`); }};
const requestSavedChatStop = async stop => (await stop()).status ? {state:'stopped'} : {state:'already-finished'};
const stopTasksByChatId = async (_token,id) => { assert.strictEqual(id, 'saved-chat'); return {status:true}; };
const getTaskIdsByChatId = async () => ({task_ids:[]});
const stopTask = async () => { throw new Error('saved stop must use the owner route'); };
"""
        assertions = r"""
(async () => {
  await stopResponse();
  assert.strictEqual(history.messages.reply.done, true);
  assert.strictEqual(persistCalls, 1);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        self._run_node(prelude + self.stop_response + assertions)

    def test_source_preserves_saved_persistence_branch_and_fresh_id_after_new_chat_clear(self):
        self.assertIn("await chatId.set('');", self.source)
        self.assertIn("const savedChat = !$temporaryChatEnabled && !isEphemeralChatId(stoppingChatId);", self.source)
        self.assertIn("if (confirmedStopped && changed && savedChat)", self.source)
        self.assertIn("_chatId = typeof _chatId === 'string' && _chatId.startsWith('local:')", self.source)
        self.assertIn(": `local:${uuidv4()}`;", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

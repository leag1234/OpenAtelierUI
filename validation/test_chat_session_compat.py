#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Focused behavior checks for the bundled chat-session compatibility helper."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
HELPER = BASE / "src/lib/utils/chat-session.js"
CHAT = BASE / "src/lib/components/chat/Chat.svelte"
LAYOUT = BASE / "src/routes/+layout.svelte"


class ChatSessionCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = HELPER.read_text(encoding="utf-8")
        source = source.replace("export async function ", "async function ")
        source = source.replace("export function ", "function ")
        checks = r"""
const assert = require('assert');
assert.strictEqual(selectChatEventChannel({event_call_channel:'events'}), 'events');
for (const config of [{event_call_channel:'chat-events'}, {event_call_channel:'EVENTS'}, {}, null]) {
  assert.strictEqual(selectChatEventChannel(config), 'chat-events');
}
const bound = [];
const socket = {
  on: (name, handler) => bound.push(['on', name, handler]),
  off: (name, handler) => bound.push(['off', name, handler])
};
const handler = () => {};
const binding = bindChatEventChannel(socket, handler, {event_call_channel:'events'});
binding.cleanup();
assert.deepStrictEqual(bound.map(row => row.slice(0,2)), [['on','events'],['off','events']]);
assert.strictEqual(normalizeTaskIds({task_id:'legacy'}).join(','), 'legacy');
assert.deepStrictEqual(normalizeTaskIds({task_ids:['a','',null,'a',4], task_id:'b'}), ['a','b']);
assert.deepStrictEqual(mergeTaskIds(['a','a'], {task_ids:['b'],task_id:'a'}), ['a','b']);
assert.strictEqual(chatResultMatchesCurrent('chat-1','chat-1'), true);
assert.strictEqual(chatResultMatchesCurrent('chat-1','chat-2'), false);
assert.strictEqual(chatResultMatchesCurrent('chat-1',null), false);
assert.strictEqual(isEphemeralChatId('local'), true);
assert.strictEqual(isEphemeralChatId('local:temporary'), true);
assert.strictEqual(isEphemeralChatId('local-work-chat'), false);
assert.strictEqual(chatTaskStopPath('saved/chat 1'), '/api/tasks/chat/saved%2Fchat%201/stop');
(async function () {
  let listCalls = 0;
  const success = await requestSavedChatStop(
    async () => ({status:true}), async () => { listCalls += 1; return {task_ids:[]}; }, () => true
  );
  assert.strictEqual(success.state, 'stopped');
  assert.strictEqual(listCalls, 0);
  const active = await requestSavedChatStop(
    async () => ({status:false}), async () => { listCalls += 1; return {task_ids:['live']}; }, () => true
  );
  assert.strictEqual(active.state, 'uncertain');
  assert.strictEqual(active.taskListResponse.task_ids[0], 'live');
  const empty = await requestSavedChatStop(
    async () => ({status:false}), async () => ({task_ids:[]}), () => true
  );
  assert.strictEqual(empty.state, 'already-finished');
  const failedStop = await requestSavedChatStop(
    async () => { throw new Error('synthetic stop failure'); }, async () => ({task_ids:[]}), () => true
  );
  assert.strictEqual(failedStop.state, 'error');
  const failedList = await requestSavedChatStop(
    async () => ({status:false}), async () => { throw new Error('synthetic list failure'); }, () => true
  );
  assert.strictEqual(failedList.state, 'error');
  let current = true;
  const navigatedDuringStop = await requestSavedChatStop(
    async () => { current = false; return {status:false}; }, async () => ({task_ids:[]}), () => current
  );
  assert.strictEqual(navigatedDuringStop.state, 'navigated');
  current = true;
  const navigatedDuringList = await requestSavedChatStop(
    async () => ({status:false}), async () => { current = false; return {task_ids:[]}; }, () => current
  );
  assert.strictEqual(navigatedDuringList.state, 'navigated');
  process.stdout.write(JSON.stringify({bound:bound.length, ids:normalizeTaskIds({task_id:'ok'}).length, listCalls}));
})().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        cls.node = subprocess.run(
            ["node", "-e", source + "\n" + checks],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_helper_behavior_runs_in_node_and_covers_legacy_and_modern_shapes(self):
        self.assertEqual(json.loads(self.node.stdout), {"bound": 2, "ids": 1, "listCalls": 1})

    def test_layout_keeps_session_guard_and_chat_bindings_have_cleanup(self):
        layout = LAYOUT.read_text(encoding="utf-8")
        chat = CHAT.read_text(encoding="utf-8")
        self.assertIn("data?.session_id === $socket.id", layout)
        self.assertIn("layoutChatEventBinding?.cleanup()", layout)
        self.assertIn("layoutChatEventBinding?.cleanup();", layout)
        self.assertIn("chatEventBinding?.cleanup();", chat)
        self.assertIn("chatResultMatchesCurrent(_chatId, $chatId)", chat)
        self.assertIn("else if (!responseMessage.done)", chat)
        self.assertIn("chatResultMatchesCurrent(stoppingChatId, $chatId)", chat)
        self.assertIn("let fetchedChat;", chat)
        self.assertIn("if (chatResultMatchesCurrent(requestedChatId, $chatId)) await goto('/')", chat)

    def _chat_function(self, name):
        source = CHAT.read_text(encoding="utf-8")
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
        self.fail(f"could not extract {name}")

    def _run_chat_component(self, name, assertions):
        prelude = """
const assert = require('assert');
let history = {currentId:'active', messages:{
  parent:{childrenIds:['active','already-done','later']},
  active:{parentId:'parent', content:'partial answer', done:false, status:{action:'search', description:'Searching'}},
  'already-done':{parentId:'parent', content:'finished answer', done:true},
  later:{parentId:'parent', content:'newer sibling', done:false}
}};
let autoScroll = false;
const scrollToBottom = () => {};
"""
        subprocess.run(
            ["node", "-e", prelude + self._chat_function(name) + "\n" + assertions],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_confirmed_stop_is_persistable_without_relabeling_later_siblings(self):
        self._run_chat_component(
            "markCurrentResponsesDone",
            """
assert.strictEqual(markCurrentResponsesDone(true, ['active','already-done']), true);
assert.strictEqual(history.messages.active.done, true);
assert.strictEqual(history.messages.active.content, 'partial answer');
assert.deepStrictEqual(history.messages.active.statusHistory, [
  {action:'search',description:'Searching'},
  {action:'response_stopped',description:'Response stopped',done:true}
]);
assert.strictEqual(history.messages['already-done'].statusHistory, undefined);
assert.strictEqual(history.messages.later.done, false);
assert.strictEqual(markCurrentResponsesDone(true, ['active']), false);
assert.strictEqual(history.messages.active.statusHistory.filter(s => s.action === 'response_stopped').length, 1);
""",
        )

    def test_nonconfirmed_stop_finishes_locally_without_stopped_status(self):
        self._run_chat_component(
            "markCurrentResponsesDone",
            """
assert.strictEqual(markCurrentResponsesDone(false, ['active']), true);
assert.strictEqual(history.messages.active.done, true);
assert.strictEqual(history.messages.active.statusHistory, undefined);
assert.strictEqual(history.messages.active.content, 'partial answer');
""",
        )

    def test_stop_save_uses_latest_history_and_reports_only_fixed_failure(self):
        persist = self._chat_function("persistStoppedChat")
        script = r"""
const assert = require('assert');
let history = {currentId:'active', messages:{
  active:{content:'partial answer',done:true,statusHistory:[{action:'search'}, {action:'response_stopped',description:'Response stopped',done:true}]}
}};
let $chatId = 'chat-1';
const localStorage = {token:'synthetic'};
const selectedModels = ['model'];
const params = {};
const chatFiles = [];
const createMessagesList = (value) => Object.keys(value.messages);
const chatResultMatchesCurrent = (wanted, current) => wanted === current;
const notices = [];
const toast = {error: (message) => notices.push(message)};
let updateChatById = async (_token, _id, value) => { saved = value; };
let saved;
""" + persist + r"""
(async () => {
  history.messages.newer = {content:'new continuation',done:false};
  await persistStoppedChat('chat-1', history);
  assert.strictEqual(saved.history.messages.newer.content, 'new continuation');
  assert.strictEqual(saved.history.messages.active.statusHistory[1].action, 'response_stopped');
  updateChatById = async () => { throw new Error('SYNTHETIC_PRIVATE_DETAIL'); };
  await persistStoppedChat('chat-1', history);
  assert.deepStrictEqual(notices, ['The response stopped, but its status could not be saved.']);
  assert.strictEqual(notices.join('').includes('SYNTHETIC_PRIVATE_DETAIL'), false);
  const callsBeforeNavigation = notices.length;
  $chatId = 'chat-2';
  await persistStoppedChat('chat-1', history);
  assert.strictEqual(notices.length, callsBeforeNavigation);
})().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

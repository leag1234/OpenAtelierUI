#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Exercise saved attachment forwarding through the selected chat branch."""

from __future__ import annotations

import subprocess
import unittest
import os
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
CHAT = BASE / "src/lib/components/chat/Chat.svelte"
UTILS = BASE / "src/lib/utils/index.ts"
NODE = os.environ.get("OPENATELIER_TEST_NODE", "node")


def extract_arrow(source: str, name: str) -> str:
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


def extract_messages_list(source: str) -> str:
    start = source.index("export const createMessagesList = ")
    end = source.index("\n};", start) + 3
    return source[start:end].replace("export const ", "const ", 1)


class SavedAttachmentContinueTests(unittest.TestCase):
    def test_reopened_selected_branch_forwards_saved_owner_file_descriptor(self):
        chat_source = CHAT.read_text(encoding="utf-8")
        utils_source = UTILS.read_text(encoding="utf-8")
        self.assertIn("chatFiles = chatContent?.files ?? [];", chat_source)

        block_start = chat_source.index("\t\tlet files = JSON.parse(JSON.stringify(chatFiles));", chat_source.index("const sendPromptSocket"))
        block_end = chat_source.index("\n\t\tscrollToBottom();", block_start)
        file_selection = chat_source[block_start:block_end]
        self.assertIn("files: (files?.length ?? 0) > 0 ? files : undefined", chat_source)
        self.assertIn('"files": form_data.get("files", None)', (BASE / "backend/open_webui/main.py").read_text(encoding="utf-8"))

        script = r"""
const assert = require('assert');
""" + extract_messages_list(utils_source) + r"""
const selectedFile = {type:'file', id:'synthetic-owned-file', name:'Community workshop.txt', status:'uploaded'};
const unselectedFile = {type:'file', id:'synthetic-other-branch-file', name:'Other branch.txt', status:'uploaded'};
const chatContent = {files:[selectedFile]};
let chatFiles = chatContent?.files ?? [];
const history = {currentId:'selected-answer', messages:{
  root:{id:'root', parentId:null, role:'user', content:'Question', files:[selectedFile]},
  'selected-answer':{id:'selected-answer', parentId:'root', role:'assistant', content:'Answer', files:[]},
  sibling:{id:'sibling', parentId:'root', role:'user', content:'Unselected branch', files:[unselectedFile]}
}};
const selectedMessages = createMessagesList(history, history.currentId);
const _history = history;
const responseMessageId = history.currentId;
const responseMessage = _history.messages[responseMessageId];
const userMessage = _history.messages[responseMessage.parentId];
""" + file_selection + r"""
assert.deepStrictEqual(selectedMessages.map(message => message.id), ['root','selected-answer']);
assert.strictEqual(files.length, 1);
assert.deepStrictEqual(files[0], selectedFile);
assert.strictEqual(files.some(file => file.id === unselectedFile.id), false);
process.stdout.write('selected saved attachment forwarded once\n');
"""
        result = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "selected saved attachment forwarded once")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Independent public-contract scenario; same client and plugins on each runtime.

Synthetic data only. No provider endpoint is used: pipe functions are local.
This client is a qualification fixture, not an existing third-party project.
"""
import argparse
import hashlib
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

PASSWORD = 'SyntheticOnly-OpenAtelier-2026!'
PLUGINS = {
    'portable_pipe': '''"""
title: Synthetic portable integration
version: 1.0
license: BSD-3-Clause
"""
class Pipe:
    def pipe(self, body: dict, __user__: dict = None) -> str:
        return "Portable integration: " + body["messages"][-1]["content"]
''',
    'legacy_sync_user': '''"""
title: Synthetic legacy synchronous user integration
version: 1.0
license: BSD-3-Clause
"""
from open_webui.models.users import Users
class Pipe:
    def pipe(self, body: dict, __user__: dict = None) -> str:
        user = Users.get_user_by_id(__user__["id"])
        return "Synchronous user: " + user.email
''',
    'current_async_user': '''"""
title: Synthetic current asynchronous user integration
version: 1.0
license: BSD-3-Clause
"""
from open_webui.models.users import Users
class Pipe:
    async def pipe(self, body: dict, __user__: dict = None) -> str:
        user = await Users.get_user_by_id(__user__["id"])
        return "Asynchronous user: " + user.email
''',
}


class Client:
    def __init__(self, url):
        self.url = url.rstrip('/')
        self.token = None
        self.results = []

    def request(self, name, method, path, body=None, token=None, raw=None, headers=None):
        payload = json.dumps(body).encode() if body is not None else raw
        request_headers = {'Content-Type': 'application/json'} if body is not None else {}
        request_headers.update(headers or {})
        credential = token if token is not None else self.token
        if credential:
            request_headers['Authorization'] = 'Bearer ' + credential
        request = urllib.request.Request(self.url + path, data=payload,
                                         method=method, headers=request_headers)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, content, response_headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, content, response_headers = error.code, error.read(), dict(error.headers)
        seconds = time.perf_counter() - started
        try:
            value = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            value = content.decode('utf-8', errors='replace')
        # Synthetic access tokens are deliberately not persisted in results.
        safe = ({k: v for k, v in value.items() if k not in ('token', 'api_key')}
                if isinstance(value, dict) else value)
        record = {'name': name, 'method': method, 'path': path, 'status': status,
                  'seconds': seconds, 'response': safe,
                  'headers': {k: v for k, v in response_headers.items()
                              if k.lower() in ('content-type', 'content-disposition',
                                               'content-security-policy', 'x-content-type-options')},
                  'response_sha256': hashlib.sha256(content).hexdigest()}
        self.results.append(record)
        return status, value, content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--output', required=True)
    parser.add_argument('--samples', type=int, default=30)
    parser.add_argument('--third-party')
    args = parser.parse_args()
    output = Path(args.output)
    assert not output.exists(), 'Retain prior run; use a fresh output'
    assert args.samples > 0
    plugins = dict(PLUGINS)
    if args.third_party:
        plugins['input_inspector'] = Path(args.third_party).read_text()
    client = Client(args.url)
    client.request('health', 'GET', '/health')
    client.request('version', 'GET', '/api/version')
    status, account, _ = client.request('first_admin_signup', 'POST', '/api/v1/auths/signup',
                                       {'name': 'Synthetic administrator',
                                        'email': 'admin@synthetic.invalid', 'password': PASSWORD})
    assert status == 200 and account.get('token'), 'Fresh synthetic administrator signup failed'
    client.token = account['token']
    try:
        client.request('models_before_plugins', 'GET', '/api/models')
        for plugin_id, content in plugins.items():
            status, _, _ = client.request('create_' + plugin_id, 'POST', '/api/v1/functions/create',
                                          {'id': plugin_id, 'name': plugin_id,
                                           'content': content, 'meta': {'description': 'Synthetic compatibility fixture'}})
            if status == 200:
                client.request('enable_' + plugin_id, 'POST', '/api/v1/functions/id/' + plugin_id + '/toggle')
        client.request('models_after_plugins', 'GET', '/api/models')
        for plugin_id in plugins:
            for streaming in (False, True):
                payload = {'model': plugin_id, 'stream': streaming,
                           'messages': [{'role': 'user', 'content': 'Preserve this integration'}]}
                client.request(plugin_id + ('_stream' if streaming else '_plain'), 'POST',
                               '/api/chat/completions', payload)
        client.request('completion_alias', 'POST', '/api/v1/chat/completions',
                       {'model': 'portable_pipe', 'stream': False,
                        'messages': [{'role': 'user', 'content': 'Alias contract'}]})
        client.request('functions_list_alias', 'GET', '/api/v1/functions/list')
        client.request('functions_list', 'GET', '/api/v1/functions/')

        chat = {'title': 'Synthetic migration project', 'models': ['portable_pipe'],
                'messages': [{'id': 'question-1', 'role': 'user', 'content': 'Choose a branch'}],
                'history': {'currentId': 'answer-b', 'messages': {
                    'question-1': {'id': 'question-1', 'parentId': None,
                                   'childrenIds': ['answer-a', 'answer-b'],
                                   'role': 'user', 'content': 'Choose a branch'},
                    'answer-a': {'id': 'answer-a', 'parentId': 'question-1', 'childrenIds': [],
                                 'role': 'assistant', 'content': 'Retained original branch'},
                    'answer-b': {'id': 'answer-b', 'parentId': 'question-1', 'childrenIds': [],
                                 'role': 'assistant', 'content': 'Selected useful continuation'}}}}
        status, created, _ = client.request('create_branched_chat', 'POST', '/api/v1/chats/new', {'chat': chat})
        if status == 200:
            client.request('read_branched_chat', 'GET', '/api/v1/chats/' + created['id'])

        boundary = 'openatelier-synthetic-boundary'
        original = b'<html><body><h1>Original synthetic file</h1><script>window.__synthetic_execution=1</script></body></html>'
        multipart = (('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="synthetic.html"\r\n'
                      'Content-Type: text/html\r\n\r\n').encode() + original + ('\r\n--' + boundary + '--\r\n').encode())
        status, uploaded, _ = client.request('upload_original_html', 'POST', '/api/v1/files/?process=false',
                                             raw=multipart, headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        if status == 200:
            file_id = uploaded['id']
            client.request('read_original_html', 'GET', '/api/v1/files/' + file_id + '/content/html')
            client.request('download_original', 'GET', '/api/v1/files/' + file_id + '/content?attachment=true')
            client.request('unauthenticated_file_refused', 'GET', '/api/v1/files/' + file_id + '/content', token='')
            member_status, member, _ = client.request('add_synthetic_member', 'POST', '/api/v1/auths/add',
                                                       {'name': 'Synthetic member', 'email': 'member@synthetic.invalid',
                                                        'password': PASSWORD, 'role': 'user'})
            if member_status == 200 and member.get('token'):
                client.request('other_user_file_refused', 'GET', '/api/v1/files/' + file_id + '/content', token=member['token'])
                client.request('nonadmin_plugin_creation_refused', 'POST', '/api/v1/functions/create',
                               {'id': 'unauthorized_plugin', 'name': 'Unauthorized synthetic fixture',
                                'content': plugins['portable_pipe'], 'meta': {}}, token=member['token'])

        timings = []
        for index in range(args.samples):
            _, _, _ = client.request('latency_portable_' + str(index), 'POST', '/api/chat/completions',
                                      {'model': 'portable_pipe', 'stream': False,
                                       'messages': [{'role': 'user', 'content': 'Deterministic latency'}]})
            timings.append(client.results[-1]['seconds'])
        ordered = sorted(timings)
        latency = {'samples': len(timings), 'p50_seconds': statistics.median(timings),
                   'p95_seconds': ordered[min(len(ordered)-1, int(len(ordered)*0.95))],
                   'includes': 'Loopback client, application and deterministic in-process plugin; no model/provider'}
        result = {'fixture': 'independent-synthetic-client-v1',
                  'plugin_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in plugins.items()},
                  'results': client.results, 'latency': latency,
                  'not_claimed': ['existing_third_party_plugin_coverage', 'full_migration',
                                  'browser_execution', 'complete_security_audit', 'product_acceptance']}
        output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'output': str(output), 'requests': len(client.results), 'latency': latency}))
    except BaseException:
        output.write_text(json.dumps({'status': 'failed_preserve_partial', 'results': client.results}, indent=2) + '\n')
        raise


if __name__ == '__main__':
    main()

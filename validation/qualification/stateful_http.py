# SPDX-License-Identifier: BSD-3-Clause
"""Independent synthetic HTTP journeys. Run only against disposable lab instances.

These checks establish a finite observed contract, never whole-product parity.
The controller owns instance isolation/restart; this client cannot launch software.
"""
import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Journey:
    def __init__(self, base, output):
        self.base, self.output = base.rstrip('/'), output
        self.trace, self.results, self.state = [], [], {}
        self.tokens = {}
        driver = Path(sys.argv[0])
        self.metadata = {'transport_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'driver_sha256':hashlib.sha256(driver.read_bytes()).hexdigest() if driver.is_file() else None,
            'started_at':datetime.now(timezone.utc).isoformat(), 'base':self.base}

    def call(self, path, role='owner', method='GET', body=None, headers=None, statuses=(200,)):
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if role in self.tokens:
            request_headers['Authorization'] = 'Bearer ' + self.tokens[role]
        raw = body
        if isinstance(body, (dict, list)):
            raw = json.dumps(body, ensure_ascii=False).encode()
            request_headers['Content-Type'] = 'application/json'
        started = time.monotonic()
        try:
            response = urlopen(Request(self.base + path, data=raw, method=method, headers=request_headers), timeout=20)
        except HTTPError as error:
            response = error
        with response:
            content = response.read(8 * 1024 * 1024 + 1)
            status, media = response.status, response.headers.get_content_type()
        if len(content) > 8 * 1024 * 1024:
            raise ValueError('response_limit')
        value = json.loads(content) if media == 'application/json' else content
        # Tokens are synthetic, but are still omitted from persisted traces.
        saved = {key: item for key, item in value.items() if key not in {'token', 'access_token'}} if isinstance(value, dict) else value
        self.trace.append({'method': method, 'path': path, 'role': role, 'status': status, 'media': media,
            'elapsed_seconds': round(time.monotonic()-started, 4), 'response_sha256': hashlib.sha256(content).hexdigest(),
            'response': saved if media == 'application/json' else {'bytes': len(content)}})
        if status not in statuses:
            self.save()
            raise ValueError(f'{method} {path}: expected {statuses}, observed {status} {media}')
        return value

    def case(self, name, function):
        begin = len(self.trace)
        try:
            function()
            result = {'id': name, 'status': 'passed'}
        except Exception as error:
            result = {'id': name, 'status': 'failed', 'reason': str(error)[:500]}
        result.update(trace_start=begin, trace_end=len(self.trace))
        self.results.append(result)
        self.save()

    def save(self):
        self.output.write_text(json.dumps({'schema_version': 1, 'metadata':self.metadata, 'results': self.results,
            'trace': self.trace, 'state': self.state,
            'limits': 'Finite HTTP state contracts only. No browser, RAG extraction, extension execution, migration or whole-product claim.'}, indent=2, ensure_ascii=False)+'\n')

    def login(self):
        for role, actual in [('admin','admin'), ('owner','user'), ('foreign','user')]:
            value = self.call('/api/v1/auths/signin', role='anonymous', method='POST',
                body={'email':'ignored@example.invalid','password':'synthetic-unused'},
                headers={'X-Qualification-User':f'qualification-{role}@example.invalid',
                    'X-Qualification-Role':actual,'X-Qualification-Name':f'Synthetic {role}'})
            require(value['role'] == actual, 'trusted role differs')
            self.tokens[role] = value['token']
            self.state[role+'_id'] = value['id']

    def seed(self, sequence_seed=20261106):
        self.namespace=str(sequence_seed)
        self.login()
        self.case('admin.denied-to-member', lambda: self.call('/api/v1/users/all', statuses=(401,403)))
        self.case('auth.anonymous-chat-denied', lambda: self.call('/api/v1/chats/', role='anonymous', statuses=(401,403)))
        self.case('settings.unicode-persistence', self.settings)
        self.case('chat.branch-create-save-read', self.chat)
        self.case('chat.foreign-read-denied', lambda: self.call('/api/v1/chats/'+self.state['chat_id'], role='foreign', statuses=(401,403,404)))
        self.case('chat.foreign-write-denied', lambda: self.call('/api/v1/chats/'+self.state['chat_id'], role='foreign', method='POST', body={'chat':{'title':'Foreign overwrite'}}, statuses=(401,403,404)))
        self.case('chat.owner-unchanged-after-denial', lambda: require(self.call('/api/v1/chats/'+self.state['chat_id'])['chat']['title']=='Community workshop saved', 'foreign write changed title'))
        self.case('chat.pin-archive-restore', self.organize)
        self.case('folders.nested-move-rename', self.folders)
        self.case('notes.private-update', self.note)
        for filename, media, data in [
            ('café.txt','text/plain','Synthetic café — preserved\n'.encode()),
            ('rows.csv','text/csv',b'name,value\nworkshop,42\n'),
            ('options.json','application/json',b'{"synthetic":true}'),
            ('opaque.bin','application/octet-stream',bytes(range(256))),
        ]:
            self.case('files.raw-roundtrip/'+filename, lambda f=filename,m=media,d=data: self.file(f,m,d))
        self.case('files.empty-rejected', lambda: self.file('empty.txt','text/plain',b'',expected_status=400))
        self.case('chat.default-share-visibility-and-revoke', self.share)
        self.case('chat.generated-state-transitions', lambda:self.generated(sequence_seed))
        self.case('restart.capture-organization-boundary', self.capture_restart_boundary)

    def capture_restart_boundary(self):
        current=self.call('/api/v1/chats/'+self.state['chat_id'])
        # Moving into a folder may change pin state as part of ordinary reference
        # behavior. Persistence compares the observed pre-close boundary, not a
        # stale state from an earlier action. Content/branch oracles stay authored.
        self.state['organization_before_restart']={key:current[key] for key in ['pinned','archived','folder_id']}

    def generated(self, seed):
        generator=random.Random(seed)
        graph={'messages':{'q':{'id':'q','parentId':None,'childrenIds':['a'],'role':'user','content':'Synthetic choices'},
            'a':{'id':'a','parentId':'q','childrenIds':[],'role':'assistant','content':'Initial choice'}},'currentId':'a'}
        created=self.call('/api/v1/chats/new',method='POST',body={'chat':{'title':'Generated workshop','models':['atlas-qwen'],
            'params':{},'history':graph,'messages':[graph['messages']['q'],graph['messages']['a']]}})
        path='/api/v1/chats/'+created['id']
        expected={'id':created['id'],'title':'Generated workshop','pinned':False,'archived':False,'history':graph,'seed':seed,'actions':[]}
        self.state['generated']=expected
        for index in range(24):
            action=generator.choice(['rename','pin','archive','branch','foreign-write'])
            expected['actions'].append(action)
            if action=='rename':
                expected['title']=f'Workshop choice {seed}-{index} — café'
                self.call(path,method='POST',body={'chat':{'title':expected['title']}})
            elif action in {'pin','archive'}:
                field='pinned' if action=='pin' else 'archived'
                self.call(path+'/'+action,method='POST');expected[field]=not expected[field]
            elif action=='branch':
                key=f'b{index}';graph['messages']['q']['childrenIds'].append(key)
                graph['messages'][key]={'id':key,'parentId':'q','childrenIds':[],'role':'assistant','content':f'Selected choice {seed}-{index}'}
                graph['currentId']=key
                self.call(path,method='POST',body={'chat':{'history':graph,'messages':[graph['messages']['q'],graph['messages'][key]]}})
            else:
                self.call(path,role='foreign',method='POST',body={'chat':{'title':'Unexpected foreign choice'}},statuses=(401,403,404))
            self.check_generated(expected)

    def check_generated(self, expected):
        actual=self.call('/api/v1/chats/'+expected['id'])
        require(actual['chat']['title']==expected['title'],'generated title differs')
        require(actual['pinned']==expected['pinned'] and actual['archived']==expected['archived'],'generated organization state differs')
        require(core_history(actual['chat']['history'])==core_history(expected['history']),'generated branch content/edges differ')

    def settings(self):
        requested = {'ui': {'locale': 'fr-FR', 'theme': 'dark', 'variables': {'WORKSHOP':'Café — 42'}}}
        self.call('/api/v1/users/user/settings/update', method='POST', body=requested)
        current = self.call('/api/v1/users/user/settings')
        require(all(current['ui'].get(key)==value for key,value in requested['ui'].items()), 'settings roundtrip differs')
        self.state['settings'] = requested

    def chat(self):
        nodes = {
            'q': {'id':'q','parentId':None,'childrenIds':['a','b'],'role':'user','content':'Plan a synthetic workshop — café'},
            'a': {'id':'a','parentId':'q','childrenIds':[],'role':'assistant','content':'Original answer'},
            'b': {'id':'b','parentId':'q','childrenIds':[],'role':'assistant','content':'Selected answer'},
        }
        body = {'title':'Community workshop draft','models':['atlas-qwen'],'params':{},
            'history':{'messages':nodes,'currentId':'b'}, 'messages':[nodes['q'],nodes['b']], 'tags':[]}
        created = self.call('/api/v1/chats/new',method='POST',body={'chat':body})
        self.state['chat_id'] = created['id']
        self.call('/api/v1/chats/'+created['id'],method='POST',body={'chat':{'title':'Community workshop saved'}})
        stored = self.call('/api/v1/chats/'+created['id'])['chat']
        require(core_history(stored['history'])==core_history(body['history']) and
            [core_message(m) for m in stored['messages']]==[core_message(m) for m in body['messages']], 'branch graph or selected chain lost')
        self.state['chat_graph'] = body['history']

    def organize(self):
        path='/api/v1/chats/'+self.state['chat_id']
        self.call(path+'/pin',method='POST')
        require(self.call(path).get('pinned') is True, 'pin not stored')
        self.call(path+'/archive',method='POST')
        require(self.call(path).get('archived') is True, 'archive not stored')
        self.call(path+'/archive',method='POST')
        require(self.call(path).get('archived') is False, 'restore not stored')

    def folders(self):
        parent = self.call('/api/v1/folders/',method='POST',body={'name':'Community workshop '+self.namespace})
        before = self.call('/api/v1/folders/',role='foreign')
        self.call('/api/v1/folders/',role='foreign',method='POST',body={'name':'Foreign child '+self.namespace,'parent_id':parent['id']},statuses=(401,403,404))
        require(self.call('/api/v1/folders/',role='foreign')==before,'denied foreign-parent creation changed visible state')
        child = self.call('/api/v1/folders/',method='POST',body={'name':'Drafts','parent_id':parent['id']})
        self.call('/api/v1/folders/'+child['id']+'/update',method='POST',body={'name':'Saved drafts'})
        self.call('/api/v1/chats/'+self.state['chat_id']+'/folder',method='POST',body={'folder_id':child['id']})
        stored=self.call('/api/v1/folders/'+child['id'])
        require(stored['parent_id']==parent['id'] and stored['name']=='Saved drafts', 'folder parent/name differs')
        require(self.call('/api/v1/chats/'+self.state['chat_id'])['folder_id']==child['id'], 'chat folder differs')
        self.call('/api/v1/folders/'+child['id'],role='foreign',statuses=(401,403,404))
        self.state['folder_id']=child['id']; self.state['parent_id']=parent['id']

    def note(self):
        note=self.call('/api/v1/notes/create',method='POST',body={'title':'Workshop note','data':{'content':'Initial'},'meta':{'synthetic':True}})
        self.state['note_id']=note['id']
        self.call('/api/v1/notes/'+note['id']+'/update',method='POST',body={'title':'Saved workshop note','data':{'content':'Preserved café'},'meta':{'synthetic':True}})
        self.call('/api/v1/notes/'+note['id'],role='foreign',statuses=(401,403,404))
        require(self.call('/api/v1/notes/'+note['id'])['data']['content']=='Preserved café', 'note update lost')

    def file(self, filename, media, data, expected_status=200):
        boundary='synthetic-qualification-boundary'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {media}\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
        result=self.call('/api/v1/files/?process=false',method='POST',body=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary},statuses=(expected_status,))
        if expected_status != 200:
            return
        path='/api/v1/files/'+result['id']
        content=self.call(path+'/content')
        # application/json downloads are parsed by the transport; compare semantic JSON there.
        expected=json.loads(data) if media=='application/json' else data
        require(content==expected,'download bytes/content differ')
        self.call(path,role='foreign',statuses=(401,403,404))
        self.call(path+'/content',role='foreign',statuses=(401,403,404))
        self.state.setdefault('files',[]).append({'id':result['id'],'filename':filename,'media':media,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})

    def share(self):
        path='/api/v1/chats/'+self.state['chat_id']
        shared=self.call(path+'/share',method='POST')
        share_id=shared.get('share_id') or shared['id']
        self.state['revoked_share_id']=share_id
        try:
            visible=self.call('/api/v1/chats/share/'+share_id)
            require(visible['chat']['title']=='Community workshop saved','shared content differs')
            # Observed pinned-reference default: creating a share does not grant
            # another ordinary user read access. Explicit public/named grants are
            # a separate contract; this fixture grants none.
            self.call('/api/v1/chats/share/'+share_id,role='foreign',statuses=(401,403,404))
        finally:
            self.call(path+'/share',method='DELETE')
        self.call('/api/v1/chats/share/'+share_id,role='foreign',statuses=(401,403,404))
        self.call(path,role='foreign',statuses=(401,403,404))

    def reopen(self, source):
        self.state=json.loads(source.read_text())['state']
        self.login()
        def chat():
            current=self.call('/api/v1/chats/'+self.state['chat_id'])
            require(core_history(current['chat']['history'])==core_history(self.state['chat_graph']),'branch graph changed after restart')
            require(current['chat']['title']=='Community workshop saved','saved title changed')
            require(all(current[key]==value for key,value in self.state['organization_before_restart'].items()),'organization state changed after restart')
        self.case('restart.chat-branches-pin',chat)
        def folder():
            require('folder_id' in self.state, 'seed did not establish the required nested folder state')
            current=self.call('/api/v1/folders/'+self.state['folder_id'])
            require(current['parent_id']==self.state['parent_id'],'parent lost after restart')
            require(self.call('/api/v1/chats/'+self.state['chat_id'])['folder_id']==self.state['folder_id'],'chat folder lost after restart')
        self.case('restart.folder-parent-and-chat-membership',folder)
        self.case('restart.foreign-chat-denied',lambda:self.call('/api/v1/chats/'+self.state['chat_id'],role='foreign',statuses=(401,403,404)))
        self.case('restart.settings',lambda:require(all(self.call('/api/v1/users/user/settings')['ui'].get(k)==v for k,v in self.state['settings']['ui'].items()),'settings changed after restart'))
        self.case('restart.note',lambda:require(self.call('/api/v1/notes/'+self.state['note_id'])['data']['content']=='Preserved café','note changed after restart'))
        self.case('restart.share-remains-revoked',lambda:self.call('/api/v1/chats/share/'+self.state['revoked_share_id'],role='foreign',statuses=(401,403,404)))
        self.case('restart.generated-session',lambda:self.check_generated(self.state['generated']))
        for item in self.state.get('files',[]):
            def check_file(item=item):
                value=self.call('/api/v1/files/'+item['id']+'/content')
                if isinstance(value, bytes): require(hashlib.sha256(value).hexdigest()==item['sha256'],'file bytes changed')
                else: require(value=={'synthetic':True},'JSON content changed')
                self.call('/api/v1/files/'+item['id']+'/content',role='foreign',statuses=(401,403,404))
            self.case('restart.file/'+item['filename'],check_file)


def require(condition, message):
    if not condition: raise ValueError(message)


def core_message(message):
    # Compare every authored field; server-added ownership annotations are recorded
    # in raw traces but are not evidence of branch loss. No content/edge is dropped.
    return {key:message[key] for key in ['id','parentId','childrenIds','role','content']}


def core_history(history):
    return {'currentId':history['currentId'],'messages':{key:core_message(value) for key,value in history['messages'].items()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reopen',type=Path)
    parser.add_argument('--sequence-seed',type=int,default=20261106)
    args=parser.parse_args()
    journey=Journey(args.base,args.output)
    try:
        if args.reopen: journey.reopen(args.reopen)
        else: journey.seed(args.sequence_seed)
    finally: journey.save()
    print(json.dumps({'passed':sum(r['status']=='passed' for r in journey.results),'failed':sum(r['status']!='passed' for r in journey.results)}))
    return 0 if journey.results and all(r['status']=='passed' for r in journey.results) else 2


if __name__=='__main__': raise SystemExit(main())

# SPDX-License-Identifier: BSD-3-Clause
"""Exercise actual direct-model guards before model/callback effects."""
import asyncio
import copy
from types import SimpleNamespace as NS
import unittest
import test_chat_file_resource_access as fixtures

class DirectModelResourceAccessTests(unittest.TestCase):
    def setUp(self):
        fixtures.ChatFileResourceAccessTests.setUp(self)
        self.request.app.state.config.ENABLE_DIRECT_CONNECTIONS=True
        fixtures.load_functions(fixtures.ROOT/'backend/open_webui/main.py',
            ['_validate_private_chat_context','chat_completed','chat_action'],self.ns)
        async def callback(*args,**kwargs):
            self.effects.append(('callback',));return {'ok':True}
        self.ns.update(chat_completed_handler=callback,chat_action_handler=callback)
        self.request.state=NS()
    def guard(self,item,body=None,user=None):
        return self.ns['_authorize_direct_model'](self.request,item,body or {},user or self.owner)
    def refuse(self,item,body=None,user=None,code=404):
        with self.assertRaises(fixtures.HTTPError) as error:self.guard(item,body,user)
        self.assertEqual(error.exception.status_code,code);self.assertEqual(self.effects,[])
    def test_non_direct_metadata_is_unchanged_and_optional_none_is_false(self):
        for item in [{},{'direct':None},{'direct':False,'info':{'meta':{'filterIds':['server-filter']}}}]:
            self.assertIs(self.guard(item),item)
        for value in [1,'true',[],{}]:self.refuse({'direct':value})
        for item in [None,[],True]:self.refuse(item)
    def test_disabled_direct_refuses_before_loading(self):
        self.request.app.state.config.ENABLE_DIRECT_CONNECTIONS=False
        body={'model':'synthetic','model_item':{'direct':True}}
        with self.assertRaises(fixtures.HTTPError) as error:
            asyncio.run(self.ns['chat_completion'](self.request,body,self.owner))
        self.assertEqual(error.exception.status_code,403);self.assertEqual(self.effects,[])
    def test_local_bindings_refuse_at_every_client_location(self):
        for key in ['filterIds','actionIds','toolIds']:
            self.refuse({'direct':True,key:['private-binding']})
            self.refuse({'direct':True,'info':{'meta':{key:['private-binding']}}})
            self.refuse({'direct':True},{key:['private-binding']})
    def test_optional_metadata_and_provider_capabilities_preserved_without_mutation(self):
        for info in [None,{'meta':None},{'meta':{'capabilities':{'vision':True},'knowledge':None}}]:
            item={'direct':True,'id':'local-provider','info':info,'params':{'temperature':0.2}}
            before=copy.deepcopy(item);result=self.guard(item)
            self.assertEqual(item,before);self.assertIsNot(result,item)
            self.assertEqual(result['params'],before['params']);self.assertEqual(result['info']['meta']['knowledge'],[])
        for info in [[],{'meta':'bad'},{'meta':{'knowledge':'bad'}},{'meta':{'knowledge':[{}]*65}}]:
            self.refuse({'direct':True,'info':info})
    def test_private_knowledge_and_secondary_refs_refuse_before_effects(self):
        cases=[{'collection_name':'file-f1'},{'collection_names':['file-f1']},
               {'type':'file','id':'f1'}, {'collection_name':'public','collection_names':['file-f1']}]
        self.knowledge['public']=NS(user_id='owner',access_control=None)
        for entry in cases:self.refuse({'direct':True,'info':{'meta':{'knowledge':[entry]}}},user=self.reader)
    def test_authorized_knowledge_is_canonical_and_input_unchanged(self):
        item={'direct':True,'info':{'meta':{'knowledge':[{'collection_name':'file-f1','name':'Workshop'},
              {'type':'file','id':'f1','file':{'id':'f1','path':'/forged'}}]}}}
        before=copy.deepcopy(item);result=self.guard(item)
        self.assertEqual(item,before)
        entries=result['info']['meta']['knowledge']
        self.assertEqual(entries[0]['name'],'Workshop');self.assertTrue(entries[0]['legacy'])
        self.assertEqual(entries[1]['file']['path'],'/synthetic/f1')
    def test_callback_handlers_refuse_foreign_files_and_direct_refs_before_callback(self):
        for handler,args in [('chat_completed',()),('chat_action',('synthetic-action',))]:
            for body in [{'files':[{'id':'f1'}]},
                         {'model_item':{'direct':True,'info':{'meta':{'knowledge':[{'collection_name':'file-f1'}]}}}}]:
                with self.assertRaises(fixtures.HTTPError):
                    asyncio.run(self.ns[handler](self.request,*args,body,self.reader))
                self.assertEqual(self.effects,[])

if __name__=='__main__':unittest.main()

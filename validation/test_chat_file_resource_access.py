# SPDX-License-Identifier: BSD-3-Clause
"""Actual client descriptor and pre-provider entry regression fixtures."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import test_retrieval_resource_access as fixtures
HTTPError, load_functions, ROOT = fixtures.HTTPError, fixtures.load_functions, fixtures.ROOT

class ChatFileResourceAccessTests(unittest.TestCase):
    def setUp(self):
        fixtures.RetrievalResourceAccessTests.setUp(self)
        file=self.files['f1']
        file.model_dump=lambda:dict(id=file.id,user_id=file.user_id,path=file.path,filename=file.filename,
            hash='authoritative-hash',data=copy.deepcopy(file.data),meta=copy.deepcopy(file.meta),access_control=file.access_control)
        load_functions(ROOT/'backend/open_webui/routers/retrieval.py',['authorize_chat_files'],self.ns)
        self.request.app.state.MODELS={}
        self.ns['status'].HTTP_404_NOT_FOUND=404
        async def model_load(*a,**kw):self.effects.append(('model-load',));raise RuntimeError('authorized model loading reached')
        self.ns.update(retrieval=NS(authorize_chat_files=self.ns['authorize_chat_files']),get_all_models=model_load,Chats=NS(get_chat_by_id=lambda _:NS(user_id='someone-else')))
        load_functions(ROOT/'backend/open_webui/main.py',['_authorize_direct_model','chat_completion'],self.ns)
    def authorize(self,files,user=None):return self.ns['authorize_chat_files'](files,user or self.owner)
    def refuses(self,files,user=None):
        with self.assertRaises(HTTPError) as error:self.authorize(files,user)
        self.assertEqual(error.exception.status_code,404);self.assertEqual(error.exception.detail,'Resource not found');self.assertEqual(self.effects,[])
    def test_native_item_id_is_opaque_and_nested_sdk_fields_are_authoritative(self):
        source=[{'type':'file','id':'f1','itemId':'opaque-item-not-a-file-id','extension':'retained',
          'file':{'id':'f1','user_id':'forged','path':'/forged','hash':'forged','data':{'content':'forged'},'extra':'retained'},
          'docs':[{'content':'forged'}],'content':'forged'}]
        before=copy.deepcopy(source);result=self.authorize(source)[0]
        self.assertEqual(source,before);self.assertEqual(result['itemId'],'opaque-item-not-a-file-id')
        for key,value in [('id','f1'),('user_id','owner'),('path','/synthetic/f1'),('hash','authoritative-hash')]:self.assertEqual(result['file'][key],value)
        self.assertEqual(result['file']['data'],{'content':'authoritative saved text'})
        self.assertEqual(result['file']['extra'],'retained');self.assertNotIn('docs',result);self.assertNotIn('content',result)
    def test_missing_foreign_conflicting_and_malformed_file_refs_refuse(self):
        cases=[{'type':'file','collection_name':'public','file':{'path':'/forged'}}, {'type':'file','itemId':'f1'}, {'type':'file','id':'missing'},
          {'type':'file','id':'f1','file':{'id':'different'}}, {'type':'file','id':'f1','file':'bad'},
          {'type':'file','id':'','file':{'id':'f1'}}, {'type':'collection','id':12,'collection_name':'public'}]
        self.knowledge['public']=NS(user_id='owner',access_control=None)
        for item in cases:
            with self.subTest(item=item):self.refuses([item])
        self.refuses([{'type':'image','id':'f1'}],self.reader)
    def test_legacy_flat_file_fields_cannot_forge_server_authority(self):
        body={'id':'f1','path':'/forged','user_id':'forged','hash':'forged',
              'filename':'forged','data':{'content':'forged'},'meta':{'forged':True}}
        result=self.authorize([body])[0]
        canonical=self.files['f1'].model_dump()
        for key in ['path','user_id','hash','filename','data','meta']:
            self.assertEqual(result[key],canonical[key],key)
    def test_nested_file_id_without_root_id_is_supported(self):
        item=self.authorize([{'type':'file','itemId':'opaque','file':{'id':'f1'}}])[0]
        self.assertEqual(item['id'],'f1');self.assertEqual(item['file']['id'],'f1')
    def test_collection_id_is_not_misread_as_file_and_client_file_ids_cannot_escape(self):
        self.knowledge['public']=NS(user_id='owner',access_control=None)
        self.assertEqual(self.authorize([{'type':'collection','id':'public'}],self.reader)[0]['id'],'public')
        self.refuses([{'type':'collection','id':'public','data':{'file_ids':['f1']}}],self.reader)
        self.refuses([{'type':'collection','id':'public','collection_names':['file-f1']}],self.reader)
    def test_inline_web_full_text_and_docs_are_bounded_cumulatively(self):
        inline={'type':'doc','context':'full','file':{'data':{'content':'Synthetic own web text.'}},'collection_name':None}
        self.assertEqual(self.authorize([inline]),[inline])
        self.assertEqual(self.authorize([{'type':'web_search','docs':[{'content':'x'*65536}]}])[0]['docs'][0]['content'],'x'*65536)
        self.refuses([{'type':'web_search','docs':[{'content':'x'*40000},{'content':'y'*40000}]}])
        self.refuses([{'type':'text','content':'\ud800'}])
    def test_collection_list_and_file_count_bounds(self):
        self.refuses([{'type':'file','id':'f1'}]*65)
        self.refuses([{'type':'collection','collection_names':'file-f1'}])
        self.refuses([{'type':'doc','collection_name':'file-f1'}],self.reader)
        self.assertEqual(self.authorize(None),None);self.assertEqual(self.authorize([]),[])
    def test_actual_chat_entry_refuses_foreign_file_before_model_filter_or_provider(self):
        body={'model':'synthetic-parity','files':[{'type':'file','id':'f1'}]}
        with self.assertRaises(HTTPError) as error:asyncio.run(self.ns['chat_completion'](self.request,body,self.reader))
        self.assertEqual(error.exception.status_code,404);self.assertEqual(self.effects,[])
    def test_actual_chat_entry_preserves_owner_flow_and_chat_owner_priority(self):
        body={'model':'synthetic-parity','files':[{'type':'file','id':'f1'}]}
        with self.assertRaisesRegex(RuntimeError,'authorized model loading'):asyncio.run(self.ns['chat_completion'](self.request,body,self.owner))
        self.assertEqual(self.effects,[('model-load',)])
        self.effects.clear()
        with self.assertRaises(HTTPError) as error:asyncio.run(self.ns['chat_completion'](self.request,{'chat_id':'foreign','files':[{'id':'f1'}]},self.reader))
        self.assertEqual(error.exception.detail,'Chat not found');self.assertEqual(self.effects,[])

if __name__=='__main__':unittest.main()

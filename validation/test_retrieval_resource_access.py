# SPDX-License-Identifier: BSD-3-Clause
"""Exercise actual own retrieval guards with inert resource/effect doubles."""
import ast
import __future__
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]
class HTTPError(Exception):
    def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail;super().__init__(detail)

def load_functions(path,names,namespace):
    nodes=[]
    for node in ast.parse(path.read_text()).body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list=[];nodes.append(node)
    assert {n.name for n in nodes}==set(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec',flags=__future__.annotations.compiler_flag),namespace)

class RetrievalResourceAccessTests(unittest.TestCase):
    def setUp(self):
        self.effects=[]
        self.files={'f1':NS(id='f1',user_id='owner',access_control=None,meta={},data={'content':'authoritative saved text'},path='/synthetic/f1',filename='Workshop.txt')}
        self.knowledge={}
        self.owner=NS(id='owner',role='user');self.reader=NS(id='reader',role='user');self.admin=NS(id='admin',role='admin')
        def update(*a,**kw):self.effects.append(('file-write',a));return True
        def query_doc(**kw):self.effects.append(('query',kw['collection_name']));return {'ok':True}
        def query_collection(**kw):self.effects.append(('query-many',kw['collection_names']));return {'ok':True}
        def embed(*a,**kw):self.effects.append(('embedding',));return [0.1,0.2]
        def save(*args,**kw):
            if args:kw.update(zip(['request','docs','collection_name'],args))
            self.effects.append(('vector-write',kw['collection_name'],[d.page_content for d in kw['docs']]));return True
        def read_path(p):self.effects.append(('file-read',));raise RuntimeError('positive owner path reached')
        self.ns={'hashlib':hashlib,'re':re,'HTTPException':HTTPError,'ERROR_MESSAGES':NS(NOT_FOUND='Resource not found',DEFAULT=lambda e:str(e)),
          'status':NS(HTTP_400_BAD_REQUEST=400),'Depends':lambda f:None,'get_verified_user':lambda:None,
          'Files':NS(get_file_by_id=lambda i:self.files.get(i),update_file_hash_by_id=update,update_file_data_by_id=update,update_file_metadata_by_id=update),
          'Knowledges':NS(get_knowledge_by_id=lambda i:self.knowledge.get(i)),
          'Groups':NS(get_groups_by_member_id=lambda i:[NS(id='readers')] if i=='reader' else []),
          'log':NS(exception=lambda *a:None,error=lambda *a:None,debug=lambda *a:None),
          'Storage':NS(get_file=read_path),'query_doc':query_doc,'query_collection':query_collection,
          'RAG_EMBEDDING_QUERY_PREFIX':'synthetic','save_docs_to_vector_db':save,
          'Document':lambda **kw:NS(**kw),'calculate_sha256_string':lambda s:hashlib.sha256(s.encode()).hexdigest(),
          'BatchProcessFilesResult':lambda **kw:NS(**kw),'BatchProcessFilesResponse':lambda **kw:NS(**kw)}
        load_functions(ROOT/'backend/open_webui/utils/access_control.py',['has_access'],self.ns)
        load_functions(ROOT/'backend/open_webui/routers/retrieval.py',['_authorized_collection','_generated_collection','_ingestion_collection','_require_collection','process_file','process_files_batch','query_doc_handler','query_collection_handler'],self.ns)
        config=NS(ENABLE_RAG_HYBRID_SEARCH=False,TOP_K=1,TOP_K_RERANKER=1,RELEVANCE_THRESHOLD=0)
        self.request=NS(app=NS(state=NS(config=config,EMBEDDING_FUNCTION=embed,rf=None)))
    def allowed(self,name,user,permission='read'):return self.ns['_authorized_collection'](name,user,permission)
    def refused_without_effect(self,function,form,user=None):
        self.effects.clear()
        with self.assertRaises(HTTPError) as error:self.ns[function](self.request,form,user or self.reader)
        self.assertEqual(error.exception.status_code,404);self.assertEqual(error.exception.detail,'Resource not found');self.assertEqual(self.effects,[])
    def test_private_null_file_acl_is_not_public_knowledge_acl(self):
        for name in ['f1','file-f1']:
            self.assertTrue(self.allowed(name,self.owner));self.assertTrue(self.allowed(name,self.owner,'write'))
            self.assertFalse(self.allowed(name,self.reader));self.assertFalse(self.allowed(name,self.reader,'write'))
        self.knowledge['k1']=NS(user_id='owner',access_control=None)
        self.assertTrue(self.allowed('k1',self.reader));self.assertFalse(self.allowed('k1',self.reader,'write'))
    def test_explicit_and_linked_grants_follow_actual_group_acl(self):
        self.files['f1'].access_control={'read':{'group_ids':['readers']}}
        self.assertTrue(self.allowed('file-f1',self.reader));self.assertFalse(self.allowed('file-f1',self.reader,'write'))
        self.files['f1'].access_control=None;self.files['f1'].meta={'collection_name':'k1'}
        self.knowledge['k1']=NS(user_id='owner',access_control={'read':{'user_ids':['reader']}})
        self.assertTrue(self.allowed('file-f1',self.reader));self.assertFalse(self.allowed('file-f1',self.reader,'write'))
    def test_unknown_ambiguous_and_invalid_names_fail_closed_for_ordinary_users(self):
        self.assertFalse(self.allowed('unknown',self.reader));self.assertTrue(self.allowed('unknown',self.admin))
        for value in [None,'',12,'x'*257]:self.assertFalse(self.allowed(value,self.admin))
        self.knowledge['f1']=NS(user_id='owner',access_control=None)
        self.assertFalse(self.allowed('f1',self.owner))
        self.knowledge['file-f1']=NS(user_id='owner',access_control=None)
        self.assertFalse(self.allowed('file-f1',self.owner))
    def test_process_nonowner_and_missing_refuse_before_loading_or_writes(self):
        for identifier in ['f1','missing']:
            self.refused_without_effect('process_file',NS(file_id=identifier,content=None,collection_name=None))
        self.refused_without_effect('process_file',NS(file_id='f1',content=None,collection_name='foreign-index'),self.owner)
    def test_owner_processing_still_reaches_its_authorized_file_path(self):
        with self.assertRaises(HTTPError):self.ns['process_file'](self.request,NS(file_id='f1',content=None,collection_name=None),self.owner)
        self.assertEqual(self.effects,[('file-read',)])
    def test_foreign_queries_and_mixed_collection_list_refuse_before_embeddings(self):
        form=NS(collection_name='file-f1',query='synthetic',k=1,k_reranker=None,r=None,hybrid=False)
        self.refused_without_effect('query_doc_handler',form)
        self.knowledge['public']=NS(user_id='owner',access_control=None)
        self.refused_without_effect('query_collection_handler',NS(collection_names=['public','file-f1'],query='synthetic'))
        self.refused_without_effect('query_collection_handler',NS(collection_names=['public']*65,query='synthetic'))
    def test_owner_query_keeps_existing_signature_and_calls(self):
        form=NS(collection_name='file-f1',query='synthetic',k=1,k_reranker=None,r=None,hybrid=False)
        self.assertEqual(self.ns['query_doc_handler'](self.request,form,self.owner),{'ok':True})
        self.assertEqual(self.effects,[('embedding',),('query','file-f1')])
    def test_batch_prevalidates_every_owner_and_destination(self):
        self.knowledge['owned']=NS(user_id='reader',access_control={})
        self.files['own']=NS(id='own',user_id='reader',access_control=None,meta={},data={'content':'saved'},path=None,filename='Own.txt')
        self.refused_without_effect('process_files_batch',NS(files=[NS(id='own'),NS(id='f1')],collection_name='owned'))
        self.refused_without_effect('process_files_batch',NS(files=[NS(id='own')],collection_name=''))
        self.refused_without_effect('process_files_batch',NS(files=[NS(id='own')]*65,collection_name='owned'))
    def test_batch_uses_persisted_text_instead_of_client_file_payload(self):
        submitted=NS(id='f1',data={'content':'forged client body'},user_id='forged',meta={'bad':True})
        result=self.ns['process_files_batch'](self.request,NS(files=[submitted],collection_name='file-f1'),self.owner)
        self.assertEqual(len(result.results),1);self.assertEqual(result.results[0].status,'completed')
        self.assertIn(('vector-write','file-f1',['authoritative saved text']),self.effects)

if __name__=='__main__':unittest.main()

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Owner-bound ingestion and collection routing with no external services."""
import asyncio
from types import SimpleNamespace as NS
import unittest
import test_retrieval_resource_access as fixtures

class CollectionNamespaceTests(unittest.TestCase):
    def setUp(self):
        fixtures.RetrievalResourceAccessTests.setUp(self)
        fixtures.load_functions(fixtures.ROOT/'backend/open_webui/routers/retrieval.py',
            ['process_text','process_web','process_youtube_video','process_web_search'],self.ns)
        config=self.request.app.state.config
        config.ENABLE_WEB_LOADER_SSL_VERIFICATION=True;config.WEB_SEARCH_CONCURRENT_REQUESTS=1
        config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=False;config.WEB_SEARCH_TRUST_ENV=False
        config.YOUTUBE_LOADER_LANGUAGE='en';config.YOUTUBE_LOADER_PROXY_URL=None;config.WEB_SEARCH_ENGINE='synthetic'
        self.url='https://synthetic.invalid/page'
        def loader(url,**kw):
            self.effects.append(('loader-created',))
            def load():self.effects.append(('page-fetch',));return [NS(page_content='Synthetic owned page',metadata={'source':self.url})]
            async def aload():return load()
            return NS(load=load,aload=aload)
        def search(*args):self.effects.append(('search',));return [NS(link=self.url)]
        async def pool(fn,*args,**kw):return fn(*args,**kw)
        self.ns.update(get_web_loader=loader,YoutubeLoader=loader,search_web=search,run_in_threadpool=pool,
                       logging=NS(info=lambda *a:None))
        self.ns['ERROR_MESSAGES'].WEB_SEARCH_ERROR=lambda e:str(e)
    def generate(self,user=None,domain='web',resource=None):return self.ns['_generated_collection'](user or self.owner,domain,self.url if resource is None else resource)
    def test_generated_name_is_stable_short_and_owner_bound(self):
        first=self.generate();self.assertEqual(first,self.generate());self.assertEqual(len(first),60)
        self.assertNotEqual(first,self.generate(self.reader));self.assertNotEqual(first,self.generate(domain='youtube'))
        self.assertTrue(self.ns['_authorized_collection'](first,self.owner,'write'))
        self.assertTrue(self.ns['_authorized_collection'](first,self.owner,'read'))
        self.assertFalse(self.ns['_authorized_collection'](first,self.reader,'read'))
    def test_persisted_resource_collision_cannot_be_bypassed_by_owner_prefix(self):
        name=self.generate();self.knowledge[name]=NS(user_id='different',access_control={})
        self.assertFalse(self.ns['_authorized_collection'](name,self.owner,'write'))
        self.assertFalse(self.ns['_authorized_collection'](name,self.owner,'read'))
    def test_explicit_known_destinations_keep_their_original_id(self):
        self.knowledge['owned']=NS(user_id='owner',access_control={})
        self.assertEqual(self.ns['_ingestion_collection']('owned',self.owner,'text','value'),'owned')
        self.assertEqual(self.ns['_ingestion_collection']('legacy-admin',self.admin,'text','value'),'legacy-admin')
        for requested in ['unknown',self.generate(self.reader),12]:
            with self.subTest(requested=requested),self.assertRaises(fixtures.HTTPError):self.ns['_ingestion_collection'](requested,self.owner,'text','value')
    def test_invalid_generated_input_refuses_cleanly(self):
        for value in [12,[], '\ud800']:
            with self.subTest(value=repr(value)),self.assertRaises(fixtures.HTTPError):self.ns['_generated_collection'](self.owner,'text',value)
        self.assertEqual(self.effects,[])
    def test_foreign_text_web_and_youtube_destinations_refuse_before_effects(self):
        for fn in ['process_text','process_web','process_youtube_video']:
            self.effects.clear();form=NS(collection_name='file-f1',content='synthetic',name='Synthetic',url=self.url)
            with self.subTest(fn=fn),self.assertRaises(fixtures.HTTPError) as error:self.ns[fn](self.request,form,self.reader)
            self.assertEqual(error.exception.status_code,404);self.assertEqual(self.effects,[])
    def test_default_text_and_web_ingestion_can_be_used_by_the_owner(self):
        text=self.ns['process_text'](self.request,NS(collection_name=None,content='Synthetic text',name='Synthetic'),self.owner)
        self.assertTrue(text['status']);self.assertTrue(self.ns['_authorized_collection'](text['collection_name'],self.owner))
        self.effects.clear()
        web=self.ns['process_web'](self.request,NS(collection_name='',url=self.url),self.owner)
        self.assertTrue(web['status']);self.assertIn(('page-fetch',),self.effects)
        self.assertTrue(self.ns['_authorized_collection'](web['collection_name'],self.owner))
        self.assertFalse(self.ns['_authorized_collection'](web['collection_name'],self.reader))
    def test_search_checks_destinations_before_page_fetch_or_vector_write(self):
        name=self.generate(domain='web-search',resource='query-'+self.url)
        self.knowledge[name]=NS(user_id='different',access_control={})
        with self.assertRaises(fixtures.HTTPError) as error:asyncio.run(self.ns['process_web_search'](self.request,NS(query='query'),self.owner))
        self.assertEqual(error.exception.status_code,404);self.assertEqual(self.effects,[('search',)])
    def test_search_default_result_names_are_readable_only_by_the_owner(self):
        result=asyncio.run(self.ns['process_web_search'](self.request,NS(query='query'),self.owner))
        self.assertTrue(result['status']);self.assertEqual(len(result['collection_names']),1)
        name=result['collection_names'][0]
        self.assertTrue(self.ns['_authorized_collection'](name,self.owner))
        self.assertFalse(self.ns['_authorized_collection'](name,self.reader))

if __name__=='__main__':unittest.main()

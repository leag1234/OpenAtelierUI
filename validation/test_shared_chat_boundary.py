# SPDX-License-Identifier: BSD-3-Clause
"""Independent denial/authorization cases for legacy shared snapshot resolution."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj

ROOT=Path(__file__).resolve().parents[1]

class SharedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.source=Obj(id="source",user_id="owner",share_id="token",meta={})
        self.snapshot=Obj(id="token",user_id="shared-source")
        self.members=[]
        store=Obj(get_chat_by_share_id=lambda key:self.snapshot if key=="token" else None,
            get_chat_by_id=lambda key:self.source if key=="source" else None)
        self.ns={"Chats":store,"Groups":Obj(get_groups_by_member_id=lambda uid:self.members),"ENABLE_ADMIN_CHAT_ACCESS":True}
        tree=ast.parse((ROOT/"backend/open_webui/routers/chats.py").read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {"_shared_grants","_shared_source_read_allowed","_authorized_shared_snapshot"}]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),"sharing-boundary","exec"),self.ns)

    def resolve(self,uid="foreign",role="user",token="token"):
        return self.ns["_authorized_shared_snapshot"](token,Obj(id=uid,role=role))

    def test_default_is_owner_only_with_explicit_admin_policy(self):
        self.assertIsNone(self.resolve())
        self.assertIs(self.resolve("owner"),self.snapshot)
        self.assertIs(self.resolve("admin","admin"),self.snapshot)
        self.ns["ENABLE_ADMIN_CHAT_ACCESS"]=False
        self.assertIsNone(self.resolve("admin","admin"))

    def test_revoked_or_mismatched_source_never_authorizes_snapshot(self):
        for value in [None,"another-token"]:
            self.source.share_id=value
            self.assertIsNone(self.resolve("owner"))
            self.assertIsNone(self.resolve("admin","admin"))
        self.source.share_id="token";self.snapshot.user_id="shared-unrelated"
        self.assertIsNone(self.resolve("owner"))

    def test_read_grant_is_named_and_write_does_not_imply_read(self):
        self.source.meta={"_oa_shared_access_grants":[{"principal_type":"user","principal_id":"invited","permission":"read"}]}
        self.assertIs(self.resolve("invited"),self.snapshot)
        self.assertIsNone(self.resolve("foreign"))
        self.source.meta["_oa_shared_access_grants"][0]["permission"]="write"
        self.assertIsNone(self.resolve("invited"))

    def test_explicit_source_read_grants_work_without_a_link(self):
        self.source.share_id=None
        self.source.meta={"_oa_shared_access_grants":[{"principal_type":"user","principal_id":"invited","permission":"read"}]}
        allowed=self.ns["_shared_source_read_allowed"]
        self.assertTrue(allowed(self.source,Obj(id="invited",role="user")))
        self.assertFalse(allowed(self.source,Obj(id="foreign",role="user")))
        self.assertFalse(allowed(self.source,Obj(id="invited",role="pending")))

    def test_malformed_internal_grants_do_not_become_authority(self):
        self.source.meta={"_oa_shared_access_grants":["not-a-grant",None,{}]}
        self.assertIsNone(self.resolve())

    def test_group_membership_is_rechecked_instead_of_cached(self):
        self.source.meta={"_oa_shared_access_grants":[{"principal_type":"group","principal_id":"group","permission":"read"}]}
        self.assertIsNone(self.resolve())
        self.members=[Obj(id="group")]
        self.assertIs(self.resolve(),self.snapshot)
        self.members=[]
        self.assertIsNone(self.resolve())

if __name__=="__main__":unittest.main()

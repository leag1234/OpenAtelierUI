# SPDX-License-Identifier: BSD-3-Clause
import sys,json
from pathlib import Path
from stateful_http import Journey,require
base,out=sys.argv[1:];j=Journey(base,Path(out));j.login()
g=j.call('/api/v1/groups/create',role='admin',method='POST',body={'name':'Synthetic sharing group','description':'Qualification only','permissions':{},'data':{}});gid=g['id']
c=j.call('/api/v1/chats/new',method='POST',body={'chat':{'title':'Group sharing','history':{'messages':{},'currentId':None},'messages':[],'models':[],'params':{}}});cid=c['id'];s=j.call('/api/v1/chats/'+cid+'/share',method='POST');sid=s.get('share_id') or s['id']
j.call('/api/v1/chats/shared/'+cid+'/access/update',method='POST',body={'access_grants':[{'principal_type':'group','principal_id':gid,'permission':'read'}]})
shared='/api/v1/chats/share/'+sid
j.case('group.nonmember-denied',lambda:j.call(shared,role='foreign',statuses=(401,403,404)))
j.call('/api/v1/groups/id/'+gid+'/users/add',role='admin',method='POST',body={'user_ids':[j.state['foreign_id']]})
j.case('group.added-member-read',lambda:j.call(shared,role='foreign'))
j.call('/api/v1/groups/id/'+gid+'/users/remove',role='admin',method='POST',body={'user_ids':[j.state['foreign_id']]})
j.case('group.removed-member-denied',lambda:j.call(shared,role='foreign',statuses=(401,403,404)))
# An imported document must not grant authority through an application-internal key.
imp=j.call('/api/v1/chats/import',method='POST',body={'chat':{'title':'Imported untrusted metadata','history':{'messages':{},'currentId':None},'messages':[]},'meta':{'_oa_shared_access_grants':[{'principal_type':'user','principal_id':j.state['foreign_id'],'permission':'read'}],'keep':'synthetic'}})
impid=imp['id'];s=j.call('/api/v1/chats/'+impid+'/share',method='POST');token=s.get('share_id') or s['id']
j.case('import.hidden-grant-does-not-authorize',lambda:j.call('/api/v1/chats/share/'+token,role='foreign',statuses=(401,403,404)))
j.case('import.ordinary-metadata-preserved',lambda:require(j.call('/api/v1/chats/'+impid)['meta'].get('keep')=='synthetic','ordinary metadata lost'))
j.save();print(json.dumps({'passed':sum(t['status']=='passed' for t in j.results),'failed':[t for t in j.results if t['status']!='passed']}))

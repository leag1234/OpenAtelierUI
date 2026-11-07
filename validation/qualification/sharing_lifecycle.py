# SPDX-License-Identifier: BSD-3-Clause
import sys,json
from pathlib import Path
from stateful_http import Journey,require
base,out,phase=sys.argv[1:4];j=Journey(base,Path(out));j.login()
u=j.call('/api/v1/auths/signin',role='anonymous',method='POST',body={'email':'ignored@example.invalid','password':'synthetic-unused'},headers={'X-Qualification-User':'qualification-invited@example.invalid','X-Qualification-Role':'user'})
j.tokens['invited']=u['token']
def deny(path,role='foreign',method='GET',body=None):return j.call(path,role=role,method=method,body=body,statuses=(401,403,404))
if phase=='seed':
 c=j.call('/api/v1/chats/new',method='POST',body={'chat':{'title':'Persistent invitation','history':{'messages':{'q':{'id':'q','role':'user','content':'Synthetic workshop secret','parentId':None,'childrenIds':[]}},'currentId':'q'},'messages':[],'models':[],'params':{}}});cid=c['id']
 s=j.call('/api/v1/chats/'+cid+'/share',method='POST');sid=s.get('share_id') or s['id'];j.state.update(cid=cid,sid=sid,invited_id=u['id'])
 j.case('default.foreign-read-denied',lambda:deny('/api/v1/chats/share/'+sid))
 j.case('default.foreign-clone-denied',lambda:deny('/api/v1/chats/'+sid+'/clone/shared',method='POST'))
 j.call('/api/v1/chats/shared/'+cid+'/access/update',method='POST',body={'access_grants':[{'principal_type':'user','principal_id':u['id'],'permission':'read'}]})
 def positive():
  s=j.call('/api/v1/chats/share/'+sid,role='invited');require(s['chat']['history']['messages']['q']['content']=='Synthetic workshop secret','shared text differs')
 j.case('invited.snapshot-content',positive)
 j.case('invited.source-read',lambda:j.call('/api/v1/chats/'+cid,role='invited'))
 j.case('invited.foreign-still-denied',lambda:deny('/api/v1/chats/share/'+sid))
 before=j.call('/api/v1/chats/shared/'+cid+'/access')
 def acl():
  deny('/api/v1/chats/shared/'+cid+'/access/update',method='POST',body={'access_grants':[]})
  require(j.call('/api/v1/chats/shared/'+cid+'/access')==before,'unauthorized mutation changed grants')
 j.case('foreign.grants-no-effect',acl)
 def clone():
  c=j.call('/api/v1/chats/'+sid+'/clone/shared',role='invited',method='POST');require(c['user_id']==u['id'],'clone belongs to wrong user')
 j.case('invited.clone-owned-copy',clone)
else:
 j.state=json.loads(Path(sys.argv[4]).read_text())['state'];cid,sid=j.state['cid'],j.state['sid']
 j.case('restart.invited-read',lambda:j.call('/api/v1/chats/share/'+sid,role='invited'))
 j.case('restart.invited-source-read',lambda:j.call('/api/v1/chats/'+cid,role='invited'))
 j.case('restart.foreign-denied',lambda:deny('/api/v1/chats/share/'+sid))
 j.call('/api/v1/chats/shared/'+cid+'/access/update',method='POST',body={'access_grants':[]})
 j.case('grant-revoked.read-denied',lambda:deny('/api/v1/chats/share/'+sid,'invited'))
 j.case('grant-revoked.clone-denied',lambda:deny('/api/v1/chats/'+sid+'/clone/shared','invited','POST'))
 j.case('grant-revoked.source-denied',lambda:deny('/api/v1/chats/'+cid,'invited'))
 j.case('grant-revoked.owner-still-reads',lambda:j.call('/api/v1/chats/share/'+sid))
 j.call('/api/v1/chats/'+cid+'/share',method='DELETE')
 j.case('link-revoked.owner-denied',lambda:deny('/api/v1/chats/share/'+sid,'owner'))
 j.case('link-revoked.admin-clone-denied',lambda:deny('/api/v1/chats/'+sid+'/clone/shared','admin','POST'))
 j.case('link-revoked.foreign-denied',lambda:deny('/api/v1/chats/share/'+sid))
 s=j.call('/api/v1/chats/'+cid+'/share',method='POST');new=s.get('share_id') or s['id']
 j.case('new-link.new-token',lambda:require(new!=sid,'revoked token reused'))
 j.case('new-link.old-token-denied',lambda:deny('/api/v1/chats/share/'+sid,'owner'))
 j.case('new-link.owner-read',lambda:j.call('/api/v1/chats/share/'+new))
 j.case('new-link.uninvited-denied',lambda:deny('/api/v1/chats/share/'+new))
j.save();print(json.dumps({'passed':sum(t['status']=='passed' for t in j.results),'failed':[t for t in j.results if t['status']!='passed']}))

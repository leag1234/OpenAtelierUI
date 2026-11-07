# SPDX-License-Identifier: BSD-3-Clause
"""Observed sharing state transitions; status agreement alone is not qualification."""
import sys,json
from pathlib import Path
from stateful_http import Journey
j=Journey(sys.argv[1],Path(sys.argv[2]));j.login()
u=j.call('/api/v1/auths/signin',role='anonymous',method='POST',body={'email':'ignored@example.invalid','password':'synthetic-unused'},headers={'X-Qualification-User':'qualification-invited@example.invalid','X-Qualification-Role':'user','X-Qualification-Name':'Synthetic invited'});j.tokens['invited']=u['token'];j.state['invited_id']=u['id']
g=j.call('/api/v1/groups/create',role='admin',method='POST',body={'name':'Matrix sharing group','description':'Synthetic transition coverage','permissions':{},'data':{}});gid=g['id']
c=j.call('/api/v1/chats/new',method='POST',body={'chat':{'title':'Matrix sharing state','history':{'messages':{'q':{'id':'q','role':'user','content':'Synthetic state matrix content','parentId':None,'childrenIds':[]}},'currentId':'q'},'messages':[],'models':[]}});cid=c['id'];j.state['source_id']=cid;tokens=[]
def grants(values):j.call('/api/v1/chats/shared/'+cid+'/access/update',method='POST',body={'access_grants':values})
def grant(kind,who,permission='read'):return {'principal_type':kind,'principal_id':who,'permission':permission}
def create_link():
 s=j.call('/api/v1/chats/'+cid+'/share',method='POST');sid=s.get('share_id') or s['id'];tokens.append(sid);return sid
allowed=(200,400,401,403,404,405,422,500,501)
def probe(stage):
 sid=tokens[-1] if tokens else cid
 operations=[('source-read','GET','/api/v1/chats/'+cid),('source-as-share','GET','/api/v1/chats/share/'+cid),('snapshot-read','GET','/api/v1/chats/share/'+sid),('source-clone','POST','/api/v1/chats/'+cid+'/clone/shared'),('snapshot-clone','POST','/api/v1/chats/'+sid+'/clone/shared'),('access-list','GET','/api/v1/chats/shared/'+cid+'/access')]
 for role in ['owner','invited','foreign','admin','anonymous']:
  for op,method,path in operations:
   value=j.call(path,role=role,method=method,statuses=allowed);t=j.trace[-1];t['stage']=stage;t['operation']=op
   if t['status']==200 and isinstance(value,dict) and value.get('chat'):
    t['semantic']={'title':value['chat'].get('title'),'content':value['chat'].get('history',{}).get('messages',{}).get('q',{}).get('content'),'owner_role':next((r for r in ['owner','invited','foreign','admin'] if j.state.get(r+'_id')==value.get('user_id')),'unresolved'),'current_message_id':value.get('current_message_id'),'response_keys':sorted(value)}
  if role in ['foreign','invited']:
   j.call('/api/v1/chats/'+cid,role=role,method='POST',body={'chat':{'title':'Unexpected matrix mutation'}},statuses=allowed);j.trace[-1].update(stage=stage,operation='source-write')
 j.save();print(stage,flush=True)
probe('private-no-link')
grants([grant('user',u['id'])]);probe('prelink-read-invitation')
grants([grant('user',u['id'],'write')]);probe('prelink-write-only')
grants([]);create_link();probe('linked-private')
grants([grant('user',u['id'])]);probe('linked-user-read')
grants([grant('group',gid)]);probe('linked-group-nonmember')
j.call('/api/v1/groups/id/'+gid+'/users/add',role='admin',method='POST',body={'user_ids':[j.state['foreign_id']]});probe('linked-group-member')
j.call('/api/v1/groups/id/'+gid+'/users/remove',role='admin',method='POST',body={'user_ids':[j.state['foreign_id']]});probe('linked-group-removed')
grants([grant('user',u['id'])]);j.call('/api/v1/chats/'+cid+'/share',method='DELETE');probe('link-and-grants-revoked')
create_link();probe('new-link-no-old-invitations')
j.state['tokens']=tokens;j.save()

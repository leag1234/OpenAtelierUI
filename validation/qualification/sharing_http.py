# SPDX-License-Identifier: BSD-3-Clause
"""Synthetic sharing discovery; observations are not pass declarations."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from stateful_http import Journey
base,out=sys.argv[1:];j=Journey(base,Path(out));j.login()
u=j.call('/api/v1/auths/signin',role='anonymous',method='POST',body={'email':'ignored@example.invalid','password':'synthetic-unused'},headers={'X-Qualification-User':'qualification-invited@example.invalid','X-Qualification-Role':'user','X-Qualification-Name':'Synthetic invited'})
j.tokens['invited']=u['token'];j.state['invited_id']=u['id']
try:
 s=j.call('/openapi.json',role='anonymous',statuses=(200,404))
 if isinstance(s,dict):
  for name in ['ChatAccessGrantsForm','ChatForm','AccessGrantModel']:
   print(name,json.dumps(s.get('components',{}).get('schemas',{}).get(name)))
except Exception as e:print('schema probe',str(e))
chat=j.call('/api/v1/chats/new',method='POST',body={'chat':{'title':'Sharing boundary fixture','history':{'messages':{'q':{'id':'q','role':'user','content':'Synthetic private workshop','parentId':None,'childrenIds':[]}},'currentId':'q'},'messages':[],'models':[],'params':{}}});cid=chat['id'];j.state['source_id']=cid
shared=j.call('/api/v1/chats/'+cid+'/share',method='POST');sid=shared.get('share_id') or shared['id'];j.state['share_id']=sid
allowed=(200,400,401,403,404,405,422,501)
def probe(stage,path,role='owner',method='GET',body=None):
 j.call(path,role=role,method=method,body=body,statuses=allowed)
 t=j.trace[-1];t['stage']=stage
 print(stage,role,method,path,t['status'],str(t['response'])[:180],flush=True)
for role in ['owner','invited','foreign','admin','anonymous']:
 probe('default',f'/api/v1/chats/share/{sid}',role)
 probe('default-clone',f'/api/v1/chats/{sid}/clone/shared',role,'POST')
for identity in [cid,sid]:probe('grant-read',f'/api/v1/chats/shared/{identity}/access')
for identity in [cid,sid]:
 probe('grant-invited',f'/api/v1/chats/shared/{identity}/access/update',method='POST',body={'access_grants':[{'principal_type':'user','principal_id':u['id'],'permission':'read'}]})
 if j.trace[-1]['status']==200:j.state['grant_resource_id']=identity;break
for role in ['owner','invited','foreign','admin','anonymous']:
 probe('invited',f'/api/v1/chats/share/{sid}',role)
probe('foreign-grant',f'/api/v1/chats/shared/{cid}/access/update','foreign','POST',{'access_grants':[{'principal_type':'user','principal_id':'*','permission':'read'}]})
resource=j.state.get('grant_resource_id',cid)
probe('public-grant',f'/api/v1/chats/shared/{resource}/access/update',method='POST',body={'access_grants':[{'principal_type':'user','principal_id':'*','permission':'read'}]})
for role in ['invited','foreign','anonymous']:probe('public',f'/api/v1/chats/share/{sid}',role)
probe('revoke',f'/api/v1/chats/{cid}/share',method='DELETE')
for role in ['owner','invited','foreign','admin','anonymous']:probe('revoked',f'/api/v1/chats/share/{sid}',role)
j.save()

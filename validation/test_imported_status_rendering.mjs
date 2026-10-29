// Primary independent regression fixture: captured synthetic status data only.
import assert from 'node:assert/strict';
import {statusDescription,statusSearchQuery,statusSearchUrls} from '../src/lib/utils/chatStatus.js';
const captured=[{action:'queries_generated',queries:[],done:false},{action:'sources_retrieved',count:1,done:true}];
const original=JSON.stringify(captured);
assert.match(statusDescription(captured[0]),/quer/i);
assert.equal(statusDescription(captured[1]),'1 source retrieved');
for(const description of [undefined,null,12,[],{},false]) assert.equal(typeof statusDescription({action:'sources_retrieved',count:1,description}),'string');
for(const status of [undefined,null,false,12,[],{},'untrusted']) assert.equal(typeof statusDescription(status),'string');
assert.equal(statusDescription({description:'Searched {{count}} sites'}),'Searched {{count}} sites');
assert.equal(statusDescription({description:'Searching "{{searchQuery}}"'}),'Searching "{{searchQuery}}"');
assert.equal(statusDescription({description:'<img src=x onerror=alert(1)>'}),'<img src=x onerror=alert(1)>'); // Returned text must still be escaped by the renderer; browser qualification required.
assert.equal(statusSearchQuery({query:{bad:true}}),null);
assert.equal(statusSearchUrls({urls:['javascript:alert(1)']}),null);
assert.equal(statusSearchUrls({urls:['https://user:password@example.invalid/']}),null);
assert.deepEqual(statusSearchUrls({urls:['https://example.invalid/source']}),['https://example.invalid/source']);
assert.equal(JSON.stringify(captured),original);
console.log(JSON.stringify({passed:true,captured_statuses:2,malformed_description_cases:6,null_or_wrong_status_cases:7,explicit_templates_preserved:2,original_exact:true,renderer_escaping_requires_actual_browser:true}));

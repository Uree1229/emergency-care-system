import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {unifiedEntry} from '../../paramedic-ui/unified-entry.mjs';

test('the default workspace and prior conversation URL share the canonical route',()=>{
  assert.deepEqual(unifiedEntry('', '/paramedic/'),{kind:'workspace',url:'/paramedic/?flow=field'});
  assert.deepEqual(unifiedEntry('?flow=field','/paramedic/quick-test.html'),{kind:'workspace',url:'/paramedic/?flow=field'});
  assert.equal(unifiedEntry('?sid=VS-1&actor_id=crew','/paramedic/index.html').url,'/paramedic/?sid=VS-1&actor_id=crew&flow=field');
});
test('an explicit old full-form recovery link remains explicit and preserves identifiers',()=>{
  assert.deepEqual(unifiedEntry('?view=full&sid=VS-1&actor_id=crew'),{kind:'legacy',url:'./legacy.html?view=full&sid=VS-1&actor_id=crew'});
});
test('the canonical entry mounts one conversation/workflow document without an iframe or main-app bootstrap',()=>{
  const read=name=>fs.readFileSync(new URL('../../paramedic-ui/'+name,import.meta.url),'utf8');
  const index=read('index.html');
  assert.equal(index,read('quick-test.html'));
  assert.match(index,/id="chat-composer"/);assert.match(index,/id="field-workflow-host"/);
  assert.match(index,/src="\.\/unified-entry\.mjs"/);assert.doesNotMatch(index,/<iframe|src="\.\/app\.js"|href="\.\/\?view=full"/i);
  assert.match(read('legacy.html'),/id="fields-form"/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseParamedicEntry} from './paramedic-ui/field-entry.mjs';
test('new entry starts the existing conversation without importing a clinical workflow',()=>assert.deepEqual(chooseParamedicEntry(''),{kind:'conversation',url:'./quick-test.html?flow=field'}));
test('saved main identity stays on the same main workflow',()=>assert.deepEqual(chooseParamedicEntry('',true),{kind:'main',field:true}));
for(const query of ['?voice_handoff=1','?flow=field','?pid=SYNTH','?sid=VS-1','?actor_id=DEMO'])
  test('explicit resume preserves its query and uses stages '+query,()=>assert.deepEqual(chooseParamedicEntry(query),{kind:'main',field:true}));
test('full detail mode always remains available',()=>assert.deepEqual(chooseParamedicEntry('?view=full&voice_handoff=1',true),{kind:'main',field:false}));

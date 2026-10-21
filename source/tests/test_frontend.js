"use strict";
// Isolated DOM/network fixture for the actual JS. Not browser-click evidence.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname,'../app/static/synthetic_demo.js'),'utf8');
class Element {
  constructor(){this.listeners={};this.children=[];this.value='';this.disabled=false;this.hidden=false;this.textContent='';}
  addEventListener(type,callback){this.listeners[type]=callback;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
}
function fixture(){
  const elements = new Map();
  const get = id => {if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
  get('profile').value='S5_THIRD_A';
  const timers = new Map();let nextTimer=1;
  const calls=[];let statusOverride=null;let startOverride=null;let sessionOverride=null;let auxOverride=null;let resetOverride=null;
  const state = {runtime_id:'unit_scene_1',scene_generation:0,revision:0,phase:'NOT_STARTED',control_unlocked:false,
    service_state:'READY',build_id:'FRONTEND_UNIT_ONLY',autonomous_effects:0,per_action_approvals:0,
    distinct_command_roots:0,resource_state:{setting:'REST',version:0},background:'Dinner',
    safety_decision:'NOT_STARTED',authorization:null,current_need:null,actions:[],audit:[]};
  const respond = (data,status=200) => Promise.resolve({ok:status<400,status,json:async()=>data});
  const context = vm.createContext({document:{getElementById:get,createElement:()=>new Element()},
    AbortController,crypto:{randomUUID},console,
    setTimeout:(fn,delay)=>{const id=nextTimer++;timers.set(id,{fn,delay});return id;},
    clearTimeout:id=>timers.delete(id),
    fetch:(url,options)=>{
      const body=options.body?JSON.parse(options.body):undefined;calls.push({url,body});
      if(url.endsWith('/status'))return statusOverride?statusOverride():respond({...state});
      if(url.endsWith('/client-event') && auxOverride)return auxOverride();
      if(url.endsWith('/session')){if(sessionOverride)return sessionOverride(body);state.control_unlocked=true;return respond({control:'LOCAL_PREVIEW_ONLY'});}
      if(url.endsWith('/start'))return startOverride?startOverride(body):respond({...state});
      if(url.endsWith('/reset')){if(resetOverride)return resetOverride(body);state.runtime_id='unit_scene_2';state.scene_generation++;state.control_unlocked=false;return respond({runtime_id:state.runtime_id,scene_generation:state.scene_generation});}
      return respond({recorded:true});
    }});
  vm.runInContext(source,context,{filename:'actual_synthetic_demo.js'});
  return {context,get,timers,calls,state,respond,setStatus:fn=>statusOverride=fn,setStart:fn=>startOverride=fn,setSession:fn=>sessionOverride=fn,setAux:fn=>auxOverride=fn,setReset:fn=>resetOverride=fn};
}
const flush = async()=>{for(let i=0;i<30;i++)await Promise.resolve()};
const submit = f => f.get('access-form').listeners.submit({preventDefault(){},submitter:f.get('unlock-preview'),isTrusted:true});
const clickStart = f => f.get('start-preview').listeners.click({currentTarget:f.get('start-preview'),isTrusted:true});
(async()=>{
  const results=[];
  {
    const f=fixture();await flush();f.get('preview-key').value='UNIT_FIXTURE_ONLY';
    await submit(f);await submit(f);await flush();
    assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,0);
    assert.equal(f.get('access-panel').hidden,false);assert.equal(f.get('start-panel').hidden,false);
    assert.equal(f.get('start-preview').disabled,false);
    results.push('Unlock/repeated Unlock never dispatch Start; controls remain in stable separate containers');
  }
  {
    const f=fixture();await flush();let release;
    f.setStatus(()=>new Promise(resolve=>{release=resolve}));
    const refreshing=vm.runInContext('refresh()',f.context);await flush();
    f.get('preview-key').value='UNIT_FIXTURE_ONLY';await submit(f);await flush();
    release({ok:true,status:200,json:async()=>({...f.state,autonomous_effects:99,revision:99})});
    await refreshing;await flush();assert.notEqual(f.get('effects').textContent,'99');
    results.push('Old polling generation cannot overwrite state after Unlock');
  }
  {
    const f=fixture();await flush();f.get('preview-key').value='UNIT_FIXTURE_ONLY';await submit(f);await flush();
    const payloads=[];f.setStart(body=>{payloads.push(body);return new Promise(()=>{})});
    const pending=clickStart(f);await flush();
    const deadline=[...f.timers.entries()].find(([,t])=>t.delay===4000);assert.ok(deadline);
    f.timers.delete(deadline[0]);deadline[1].fn();await pending;await flush();
    f.setStart(body=>{payloads.push(body);return f.respond({...f.state})});
    await clickStart(f);await flush();assert.equal(payloads.length,2);
    assert.deepEqual(payloads[0],payloads[1]);
    results.push('Lost Start response retries exactly the same profile/runtime/correlation');
  }
  {
    const f=fixture();await flush();f.get('preview-key').value='UNIT_FIXTURE_ONLY';await submit(f);await flush();
    f.state.control_unlocked=false;await vm.runInContext('refresh()',f.context);await flush();
    assert.equal(f.get('preview-key').disabled,false);assert.equal(f.get('start-preview').disabled,true);
    assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,0);
    results.push('Server-side session expiry restores Unlock and never starts a scene');
  }

  {
    const f=fixture();await flush();f.get('preview-key').value='SYNTHETIC_INPUT';
    f.setAux(()=>f.respond({},503));await submit(f);await flush();
    assert.equal(f.calls.filter(c=>c.url.endsWith('/session')).length,1);
    assert.equal(f.get('start-preview').disabled,false);
    assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,0);
    results.push('Auxiliary callback failure does not block authentication or manufacture a wrong-code result');
  }
  {
    const f=fixture();await flush();f.get('preview-key').value='SYNTHETIC_INPUT';await submit(f);await flush();
    f.state.phase='FINISHED';await vm.runInContext('refresh()',f.context);await flush();
    let late;
    f.setStatus(()=>new Promise(resolve=>{late=resolve}));
    const old=vm.runInContext('refresh()',f.context);await flush();
    await f.get('new-scene').listeners.click();await flush();
    assert.equal(vm.runInContext('runtimeId',f.context),'unit_scene_2');
    assert.equal(f.get('preview-key').disabled,false);
    f.get('preview-key').value='SAME_SYNTHETIC_INPUT';await submit(f);await flush();
    assert.equal(f.calls.filter(c=>c.url.endsWith('/session')).at(-1).body.runtime_id,'unit_scene_2');
    late({ok:true,status:200,json:async()=>({...f.state,runtime_id:'unit_scene_1',scene_generation:0,revision:99,autonomous_effects:99})});
    await old;await flush();
    assert.equal(vm.runInContext('runtimeId',f.context),'unit_scene_2');
    assert.notEqual(f.get('effects').textContent,'99');
    assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,0);
    results.push('Reset response immediately binds new scene; re-unlock needs no new status; late old status cannot overwrite it');
  }
  {
    for(const status of [401,410,422,503,403]){
      const f=fixture();await flush();f.get('preview-key').value='SYNTHETIC_INPUT';
      f.setSession(()=>f.respond({},status));await submit(f);await flush();
      assert.equal(f.get('preview-key').disabled,false);
      assert.equal(f.get('start-preview').disabled,true);
      assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,0);
      assert.ok(!f.get('control-message').textContent.includes('New preview process'));
      f.setSession(()=>{f.state.control_unlocked=true;return f.respond({control:'LOCAL_PREVIEW_ONLY'})});
      f.get('preview-key').value='SYNTHETIC_INPUT';await submit(f);await flush();
      assert.equal(f.get('start-preview').disabled,false);
    }
    results.push('401/410/422/503/403 have distinct recoverable messages and no permanent unlocking state');
  }
  {
    const f=fixture();await flush();let release;let calls=0;
    f.setStatus(()=>{calls++;return new Promise(resolve=>{release=resolve})});
    const pending=vm.runInContext('refresh()',f.context);await flush();
    await vm.runInContext('refresh()',f.context);await flush();assert.equal(calls,1);
    const timeout=[...f.timers.entries()].find(([,t])=>t.delay===4000);assert.ok(timeout);
    f.timers.delete(timeout[0]);timeout[1].fn();await pending;await flush();
    assert.equal(vm.runInContext('pollRequest',f.context),null);
    assert.ok(f.get('service-state').textContent.includes('Disconnected'));
    release({ok:true,status:200,json:async()=>({...f.state,revision:99,autonomous_effects:99})});await flush();
    assert.notEqual(f.get('effects').textContent,'99');
    f.setStatus(()=>f.respond({...f.state}));await vm.runInContext('refresh()',f.context);await flush();
    assert.equal(f.get('service-state').textContent,'READY');
    results.push('Hung poll is bounded, single-flight, rejects late data and recovers without Start');
  }
  console.log(JSON.stringify({kind:'ACTUAL_JS_ISOLATED_FIXTURE',browser_click_evidence:false,passed:results.length,results},null,2));
})().catch(error=>{console.error(error.stack);process.exitCode=1});

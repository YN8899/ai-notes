import {createContext,useContext,useEffect,useRef,type ReactNode} from 'react'
import {api} from '../../api'
import {authSession,readSession} from '../../session'
import {createUuid} from '../../uuid'
import {activityHeartbeatMs,createActivityClock,createActivityQueue,type ActivityKind,type ActivityResource,type ActivityTarget} from './activity'
type Tracker={register:(target:ActivityTarget)=>()=>void}
const ActivityContext=createContext<Tracker|null>(null)
export function LearningActivityProvider({owner,sessionId,children}:{owner:string;sessionId:string;children:ReactNode}){
  const registration=useRef<{token:symbol;target:ActivityTarget}|null>(null),runtime=useRef<Pick<ReturnType<typeof createActivityClock>,'target'>|null>(null),tracker=useRef<Tracker>({register(target){const token=Symbol();registration.current={token,target};runtime.current?.target(target,Date.now());return()=>{if(registration.current?.token===token){runtime.current?.target(null,Date.now());registration.current=null}}}})
  useEffect(()=>{
    const ticket=authSession.ticket(),active=()=>authSession.current(ticket,owner)&&ticket.id===sessionId
    if(!active())return
    const scope=readSession()?.scope||sessionId,key='ai-notes:activity:v1:'+owner+':'+scope
    // Each tab/session owns its queue. Clear any other identity on this tab.
    try{for(let i=sessionStorage.length-1;i>=0;i--){const candidate=sessionStorage.key(i);if(candidate?.startsWith('ai-notes:activity:v1:')&&candidate!==key)sessionStorage.removeItem(candidate)}}catch{}
    let clock:ReturnType<typeof createActivityClock>
    const rejected=new Set<string>(),resourceKey=(target:ActivityTarget)=>JSON.stringify([target.kind,target.resource.note_id||'',target.resource.card_id||'',target.resource.quiz_id||''])
    const queue=createActivityQueue({key,storage:sessionStorage,now:Date.now,active,send:events=>api('/learning/events','POST',{events}),onReject:events=>{for(const event of events)rejected.add(resourceKey({kind:event.kind,resource:event}));if(registration.current&&rejected.has(resourceKey(registration.current.target)))clock.target(null,Date.now())}})
    clock=createActivityClock(event=>queue.enqueue(event),createUuid);runtime.current={target:(target,now)=>clock.target(target&&rejected.has(resourceKey(target))?null:target,now)}
    clock.visibility(document.visibilityState==='visible',Date.now());if(registration.current)runtime.current.target(registration.current.target,Date.now())
    const input=(event:Event)=>{if(!event.isTrusted||!active())return;const now=Date.now();if(['pointerdown','keydown','touchstart'].includes(event.type))clock.intent(now);if(registration.current&&!rejected.has(resourceKey(registration.current.target)))clock.input(now)}
    const visible=()=>{clock.visibility(document.visibilityState==='visible',Date.now());if(document.visibilityState!=='visible')void queue.drain()}
    const drain=()=>{if(navigator.onLine!==false&&active())void queue.drain()}
    const interval=setInterval(()=>{if(!active())return;clock.tick(Date.now());drain()},activityHeartbeatMs)
    const unsubscribe=authSession.subscribe(()=>{if(!active()){clock.target(null,Date.now());queue.clear()}})
    const hide=()=>{clock.visibility(false,Date.now());drain()}
    // Scrolling is observed through its wheel/touch/key/pointer gestures;
    // DOM scroll events also fire for focus/scrollIntoView and are not intent.
    const events=['pointerdown','pointermove','keydown','touchstart','touchmove','wheel'] as const
    events.forEach(event=>window.addEventListener(event,input,{passive:true}));document.addEventListener('visibilitychange',visible);window.addEventListener('online',drain);window.addEventListener('pagehide',hide)
    drain()
    return()=>{clock.stop(Date.now());runtime.current=null;clearInterval(interval);unsubscribe();events.forEach(event=>window.removeEventListener(event,input));document.removeEventListener('visibilitychange',visible);window.removeEventListener('online',drain);window.removeEventListener('pagehide',hide);if(active()){void queue.drain();queue.pause()}else queue.clear()}
  },[owner,sessionId])
  return <ActivityContext.Provider value={tracker.current}>{children}</ActivityContext.Provider>
}
export function useLearningActivity(kind:ActivityKind,resource:ActivityResource|null){const tracker=useContext(ActivityContext),note=resource?.note_id,card=resource?.card_id,quiz=resource?.quiz_id;useEffect(()=>{if(!tracker||!note&&!card&&!quiz)return;return tracker.register({kind,resource:{...(note?{note_id:note}:{}),...(card?{card_id:card}:{}),...(quiz?{quiz_id:quiz}:{})}})},[tracker,kind,note,card,quiz])}

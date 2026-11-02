import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api,ApiError,date} from '../../api'
import {NoteError} from '../notes/NoteError'
import type {PreferencesOut,RecommendationPage} from './types'
const signalNames:Record<string,string>={manual_subjects:'本人手动学科兴趣',manual_tags:'本人手动标签兴趣',own_recent_note_tags:'已授权本人近30天最多200条活动所涉笔记标签'}
export function RecommendationsPanel({owner,sessionId,preferences,onRead,onPreferences,onLatest}:{owner:string;sessionId:string;preferences:PreferencesOut;onRead:(id:string)=>void;onPreferences:()=>void;onLatest:()=>void}){
  const [cursor,setCursor]=useState(''),[epoch,setEpoch]=useState(0)
  const feed=useQuery({queryKey:['personalization',owner,sessionId,'feed',preferences.revision,cursor,epoch],queryFn:()=>api<RecommendationPage>('/personalization/recommendations?limit=20'+(cursor?'&cursor='+encodeURIComponent(cursor):'')),retry:false})
  const reset=feed.error instanceof ApiError&&feed.error.status===409
  function first(){setCursor('');setEpoch(v=>v+1)}
  return <section className="panel personalization-panel" aria-label="个性化社区推荐"><div className="panel-heading"><h2>{preferences.recommendations_enabled?'个性化社区推荐':'社区最新发布'}</h2><button className="text-button" onClick={onPreferences}>管理推荐偏好</button></div><p className="muted">{preferences.recommendations_enabled?'仅排序当前可见的已发布社区帖子。个人信号来自本人明确授权，不改变帖子权限。':'个性化推荐已关闭，按发布时间展示，不使用个人信号。'} 原社区“最新 / 关注 / 规则推荐”保持独立入口。</p><div className="social-actions"><button className="secondary" onClick={onLatest}>打开社区最新列表</button></div><NoteError error={feed.error}/>{reset?<p role="alert">本人偏好或信号已变化，请从第一页重新核对。旧分页内容不会继续展示。<button className="secondary" onClick={first}>清除分页并重新读取</button></p>:null}
    {feed.data&&!feed.error?<><p className="muted">本页使用信号：{feed.data.reasons_used.length?feed.data.reasons_used.map(s=>signalNames[s]||s).join('；'):'无，按发布时间'}。阅读和活动不是掌握度。</p>{feed.data.items.length?feed.data.items.map(post=><article className="personalization-post" key={post.id}><h3><button className="text-button" onClick={()=>onRead(post.id)}>{post.title}</button></h3><p className="muted">{post.author.display_name} · {date(post.published_at)}</p><p>{post.excerpt||'打开公开帖子阅读完整内容。'}</p><p>推荐理由：{post.recommendation_reason}</p><p className="muted">{post.subject} {post.tags.map(t=>'#'+t).join(' ')} · {post.like_count} 个赞 · {post.comment_count} 条讨论</p><button className="secondary" onClick={()=>onRead(post.id)}>阅读与讨论</button></article>):<p>暂无可见帖子。</p>}<div className="social-actions"><button className="secondary" disabled={feed.isFetching} onClick={first}>重新读取第一页</button><button className="secondary" disabled={feed.isFetching||!feed.data.next_cursor} onClick={()=>setCursor(feed.data?.next_cursor||'')}>下一页推荐</button></div></>:feed.isPending?<p role="status">正在读取社区内容…</p>:!reset?<button className="secondary" onClick={()=>void feed.refetch()}>重试读取</button>:null}
  </section>
}

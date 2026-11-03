import { useEffect, useRef, useState } from 'react'
import { authSession } from '../../session'
import { NoteError } from '../notes/NoteError'
import { fetchExportArchive, startArchiveDownload, type ExportProgress } from './download'
import './exports.css'

const mib = (bytes: number) => (bytes / 1024 / 1024).toFixed(1)

export function ExportPanel({ owner }: { owner: string }) {
  const [busy, setBusy] = useState(false), [progress, setProgress] = useState<ExportProgress | null>(null)
  const [message, setMessage] = useState(''), [error, setError] = useState<unknown>(null)
  const controller = useRef<AbortController | null>(null), alive = useRef(true)
  const releaseUrl = useRef<(() => void) | null>(null), releaseTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; controller.current?.abort(); clearTimeout(releaseTimer.current); releaseUrl.current?.(); releaseUrl.current = null }
  }, [])
  async function download() {
    if (busy) return
    const ticket = authSession.ticket(), request = new AbortController()
    controller.current = request
    const current = () => alive.current && authSession.current(ticket, owner)
    setBusy(true); setError(null); setMessage(''); setProgress({ phase: 'preparing', received: 0 })
    try {
      const archive = await fetchExportArchive(owner, request.signal, value => { if (current()) setProgress(value) })
      if (!current()) return
      releaseUrl.current?.(); clearTimeout(releaseTimer.current)
      releaseUrl.current = startArchiveDownload(archive, request.signal)
      releaseTimer.current = setTimeout(() => { releaseUrl.current?.(); releaseUrl.current = null }, 1000)
      setMessage('下载已交给浏览器，请在浏览器下载列表中确认文件已保存。')
    } catch (failure) {
      if (current()) {
        if (request.signal.aborted) setMessage('已取消导出，可以重新生成。')
        else setError(failure)
      }
    } finally {
      if (controller.current === request) controller.current = null
      if (current()) { setBusy(false); setProgress(null) }
    }
  }
  return <section className="panel export-panel" aria-labelledby="archive-export-heading">
    <h3 id="archive-export-heading">完整导出</h3>
    <p>将当前账号的笔记（含回收站）、文件与历史版本、AI 对话及工具历史、学习卡片、测验与作答、复习计划和日志、学习记录、首页偏好与学习分析，以及本人社交/社区历史和合法可读附件一起打包为 ZIP v5。</p>
    <p className="muted">只包含已同步到服务器的内容。请等待笔记保存、上传和后台转换完成后再导出。此 ZIP 用于归档，目前尚无一键导入功能。</p>
    <p className="muted">单次未压缩内容最多 512 MiB。下载期间浏览器会临时使用内存接收文件，请保持页面打开；打包较久或网络中断时可以重试。</p>
    {progress ? <p role="status">{progress.phase === 'preparing' ? '正在生成完整导出文件…' : `正在接收 ZIP：${mib(progress.received)} MiB${progress.total === undefined ? '' : ' / ' + mib(progress.total) + ' MiB'}`}</p> : null}
    {message ? <p role="status">{message}</p> : null}
    <NoteError error={error}/>
    <div className="note-actions"><button type="button" className="secondary" disabled={busy} onClick={() => void download()}>{busy ? '正在准备下载…' : '生成并下载 ZIP'}</button>
      {busy ? <button type="button" className="text-button" onClick={() => controller.current?.abort()}>取消导出</button> : null}</div>
  </section>
}

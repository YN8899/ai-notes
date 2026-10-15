import { ApiError } from '../../api'
export function NoteError({error}:{error:unknown}) {
  return error?<div className="error" role="alert">{error instanceof Error?error.message:String(error)}{error instanceof ApiError?<small>请求标识：{error.requestId}</small>:null}</div>:null
}

import {useMemo} from 'react'
import {MarkdownManager} from '@tiptap/markdown'
import StarterKit from '@tiptap/starter-kit'
import {FormattedAnswer} from '../ai/FormattedAnswer'
// Reuse installed Markdown parser and the existing allowlisted React renderer.
// Raw HTML is never inserted into the page; renderer ignores unsupported node types/URLs.
export default function AnalysisAnswer({answer}:{answer:string}){
  const body=useMemo(()=>{try{if(new TextEncoder().encode(answer).byteLength>262144)return null;return new MarkdownManager({extensions:[StarterKit]}).parse(answer)}catch{return null}},[answer])
  return body?<FormattedAnswer body={body}/>:<div className="analysis-answer-plain">{answer}</div>
}

import {EditorContent,useEditor} from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import {BlockMath,InlineMath} from '@tiptap/extension-mathematics'
import 'katex/dist/katex.min.css'
export default function ReadOnlyBody({body,plainText=''}:{body:Record<string,unknown>;plainText?:string}){
  const editor=useEditor({extensions:[StarterKit,BlockMath.configure({katexOptions:{trust:false,throwOnError:false,maxExpand:1000,maxSize:10}}),InlineMath.configure({katexOptions:{trust:false,throwOnError:false,maxExpand:1000,maxSize:10}})],content:Object.keys(body).length?body:{type:'doc',content:plainText.split('\n').map(text=>({type:'paragraph',content:text?[{type:'text',text}]:[]}))},editable:false,editorProps:{attributes:{class:'note-prose','aria-label':'只读笔记正文'}}},[body,plainText])
  return <EditorContent editor={editor}/>
}

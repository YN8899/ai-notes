type Node={type:string;text?:string;attrs?:{latex:string};content?:Node[]}

// Keep OCR text as text. Only explicitly delimited formulae become math nodes;
// the existing renderer bounds KaTeX and disables trusted commands / HTML.
export function ocrPreviewBody(text:string):Node {
  const content:Node[]=[],paragraph:Node[]=[]
  const flush=()=>{if(paragraph.length){content.push({type:'paragraph',content:paragraph.splice(0)});}}
  const plain=(value:string)=>{if(value)paragraph.push({type:'text',text:value})}
  let offset=0,index=0
  while(index<text.length) {
    const opening=text.startsWith('$$',index)?'$$':text.startsWith('\\[',index)?'\\[':text.startsWith('\\(',index)?'\\(':text[index]==='$'&&text[index-1]!=='\\'?'$':''
    if(!opening){index++;continue}
    const closing=opening==='\\['?'\\]':opening==='\\('?'\\)':opening
    const start=index+opening.length
    let end=text.indexOf(closing,start)
    if(opening==='$')while(end>=0&&(text[end-1]==='\\'||text[end+1]==='$'))end=text.indexOf(closing,end+1)
    // An unclosed delimiter remains literal. Stop instead of repeatedly scanning
    // a potentially long, malformed transcript for the same absent closer.
    if(end<0)break
    const block=opening==='$$'||opening==='\\['
    if(end===start||!block&&text.slice(start,end).includes('\n')){index=start;continue}
    plain(text.slice(offset,index))
    const latex=text.slice(start,end)
    if(block){flush();content.push({type:'blockMath',attrs:{latex}})}
    else paragraph.push({type:'inlineMath',attrs:{latex}})
    index=end+closing.length;offset=index
  }
  plain(text.slice(offset));flush()
  return {type:'doc',content}
}

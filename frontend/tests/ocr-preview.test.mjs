import assert from 'node:assert/strict'
import {test} from 'node:test'
import {ocrPreviewBody} from '../src/features/search/ocr-preview.ts'

test('formula preview preserves surrounding transcript and distinguishes inline/block delimiters',()=>{
  assert.deepEqual(ocrPreviewBody('文字 $E=mc^2$\n$$x^2$$\n\\(a+b\\) 与 \\[z=1\\]'),{type:'doc',content:[
    {type:'paragraph',content:[{type:'text',text:'文字 '},{type:'inlineMath',attrs:{latex:'E=mc^2'}},{type:'text',text:'\n'}]},
    {type:'blockMath',attrs:{latex:'x^2'}},
    {type:'paragraph',content:[{type:'text',text:'\n'},{type:'inlineMath',attrs:{latex:'a+b'}},{type:'text',text:' 与 '}]},
    {type:'blockMath',attrs:{latex:'z=1'}},
  ]})
})
test('HTML, unmatched delimiters and escaped dollars remain plain text nodes',()=>{
  const text='<script>alert(1)</script> \\$5 and unmatched $formula'
  assert.deepEqual(ocrPreviewBody(text),{type:'doc',content:[{type:'paragraph',content:[{type:'text',text}]}]})
})
test('large malformed delimiter sequences stay literal without repeated regex rescans',()=>{
  const text='\\['.repeat(32000)
  assert.deepEqual(ocrPreviewBody(text),{type:'doc',content:[{type:'paragraph',content:[{type:'text',text}]}]})
})

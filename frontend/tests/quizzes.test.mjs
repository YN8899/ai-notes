import assert from 'node:assert/strict'
import {test} from 'node:test'
import {ApiError} from '../src/api.ts'
import {newQuestion,validateQuiz,questionInput,pendingKey,reviewQuestion,ambiguous} from '../src/features/learning/quizzes.ts'
const questions=[
 {...newQuestion(),prompt:'单选',options:['A','B'],correct_answer:1},
 {...newQuestion('multiple_choice'),prompt:'多选',options:['A','B','C'],correct_answer:[0,2]},
 {...newQuestion('true_false'),prompt:'判断',correct_answer:false},
 {...newQuestion('fill_blank'),prompt:'{{blank}} 与 {{blank}}',correct_answer:['A','B']},
 {...newQuestion('short_answer'),prompt:'简答',correct_answer:'参考',explanation:'标准'},
]
test('five authored kinds validate together with 1–100 integer points',()=>{assert.doesNotThrow(()=>validateQuiz('测验',questions));for(const max_points of [0,101,1.5,true])assert.throws(()=>validateQuiz('测验',[{...questions[0],max_points}]))})
test('quiz batch validates every question and limits count at 50',()=>{assert.doesNotThrow(()=>validateQuiz('测验',Array(50).fill(questions[0])));assert.throws(()=>validateQuiz('测验',[]));assert.throws(()=>validateQuiz('测验',Array(51).fill(questions[0])));assert.throws(()=>validateQuiz('测验',[...questions,{...questions[0],prompt:' '}]))})
test('choices require unique nonempty bounded options and valid strict indices',()=>{for(const options of [['A',' A '],['A',' '],['A'],Array(9).fill('A')])assert.throws(()=>validateQuiz('测验',[{...questions[0],options}]));for(const correct_answer of [true,1.1,2,-1])assert.throws(()=>validateQuiz('测验',[{...questions[0],correct_answer}]));for(const correct_answer of [[],[0,0],[true],[3]])assert.throws(()=>validateQuiz('测验',[{...questions[1],correct_answer}]))})
test('true_false and short_answer preserve strict author answer types',()=>{for(const correct_answer of [1,'true',null])assert.throws(()=>validateQuiz('测验',[{...questions[2],correct_answer}]));for(const correct_answer of ['',false,[]])assert.throws(()=>validateQuiz('测验',[{...questions[4],correct_answer}]))})
test('fill_blank requires one bounded nonempty answer per marker',()=>{for(const correct_answer of [[],['A'],['A',' '],[true,'B']])assert.throws(()=>validateQuiz('测验',[{...questions[3],correct_answer}]));assert.throws(()=>validateQuiz('测验',[{...questions[3],prompt:'no marker'}]))})
test('source conversion sends only writable scoped selection, preserving historical revision',()=>{const source={kind:'image',note_id:'n',revision:4,title:'历史标题',mode:'region',pages:[{page_id:'p',number:2,region:{x:0,y:0,width:.5,height:.5}}]};const q={...questions[0],id:'q',position:0,source,source_valid:false,source_error:'gone'};assert.deepEqual(questionInput(q).source,{kind:'image',note_id:'n',revision:4,page_id:'p',region:source.pages[0].region});assert.equal('id'in questionInput(q),false);assert.equal('source_valid'in questionInput(q),false)})
test('pending storage identity isolates account, tab scope, operation and attempt',()=>{const key=pendingKey('a','tab','attempt:x');for(const identity of [['b','tab','attempt:x'],['a','tab2','attempt:x'],['a','tab','attempt:y'],['a','tab','start']])assert.notEqual(key,pendingKey(...identity))})
test('manual history links immutable answer IDs to questions, never positional order',()=>{const a={answers:[{id:'answer-B',question_id:'question-B'},{id:'answer-A',question_id:'question-A'}]};assert.equal(reviewQuestion(a,'answer-A'),'question-A');assert.equal(reviewQuestion(a,'missing'),undefined)})
test('uncertain transport and 5xx never authorize a fresh request key',()=>{assert.equal(ambiguous(new TypeError('offline')),true);assert.equal(ambiguous(new ApiError(500,'server','','')),true);assert.equal(ambiguous(new ApiError(409,'conflict','','')),false)})

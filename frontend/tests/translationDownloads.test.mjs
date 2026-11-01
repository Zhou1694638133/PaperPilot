import assert from 'node:assert/strict'
import test, {before, after} from 'node:test'
import {createServer} from 'vite'
import * as React from 'react'
import {renderToStaticMarkup as render} from 'react-dom/server'
let server, Downloads
before(async () => {
  server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}})
  Downloads=(await server.ssrLoadModule('/src/TranslationDownloads.tsx')).TranslationDownloads
})
after(async()=>{await server?.close()})
const base={jobId:'saved-job',ready:true,status:'已完成',modelReady:false,busy:false,typesetAvailable:false,onTypeset:()=>{},onJobAction:()=>{}}
function html(props){return render(React.createElement(Downloads,{...base,...props}))}
test('Chinese tab exposes only Chinese PDF download',()=>{
 const page=html({variant:'chinese'})
 assert.match(page,/href="\/api\/jobs\/saved-job\/chinese-text.pdf"/)
 assert.doesNotMatch(page,/href="\/api\/reports\/pdf/)
 assert.doesNotMatch(page,/下载双语 Markdown/)
})
test('bilingual tab exposes only bilingual PDF and Markdown',()=>{
 const page=html({variant:'bilingual'})
 assert.match(page,/下载双语 PDF/)
 assert.match(page,/下载双语 Markdown/)
 assert.doesNotMatch(page,/chinese-text.pdf|生成中文版 PDF|下载中文版 PDF/)
})
test('unfinished translations have no downloadable links in either tab',()=>{
 for(const variant of ['chinese','bilingual']) {
  const page=html({variant,ready:false})
  assert.doesNotMatch(page,/href=/)
  assert.match(page,/disabled/)
 }
})
test('LaTeX Chinese tab retains completed typeset output without a model',()=>{
 const page=html({variant:'chinese',typesetAvailable:true,chineseJob:{id:'typeset-job',status:'succeeded'}})
 assert.match(page,/href="\/api\/jobs\/typeset-job\/chinese.pdf"/)
 assert.doesNotMatch(page,/下载双语 PDF/)
})

test('local source recovery is available before translation and without model',()=>{
 const page=html({variant:'chinese',ready:false,onRestoreSource:()=>{}})
 assert.match(page,/恢复原图并启用图文版/)
 assert.match(page,/<button class="button primary"><svg/)
 assert.doesNotMatch(page,/href=/)
})

import type { Job, Paper } from './types'
import { statusLabel, terminalStatus } from './api'

type Point = {text: string; basis: 'author_claim'|'analysis'|'proposal'|'unknown'; evidence: string[]}
type Analysis = {document_hash: string; unknowns: string[]; evidence_locations: {paragraph_id:string;quote:string;section:string;location:string}[]} & Record<'problem'|'method'|'innovation'|'experiments'|'limitations'|'research_ideas',Point[]>
const headings = [['problem','研究问题'],['method','研究方法'],['innovation','创新点（作者主张）'],['experiments','实验与结果（作者报告）'],['limitations','局限与待核实事项'],['research_ideas','可借鉴的研究思路（分析建议）']] as const
const labels = {author_claim:'作者陈述', analysis:'分析判断', proposal:'研究建议', unknown:'尚未核实'}

export function OverallAnalysis({paper,jobs,documentHash,available,busy,onRun,onJobAction,onEvidence}: {
  paper:Paper;jobs:Job[];documentHash:string;available:boolean;busy:string|null
  onRun:(skill:string,scope:'fulltext',acknowledge:boolean,sourceJob?:string)=>void
  onJobAction:(job:Job,action:'cancel'|'retry')=>void
  onEvidence:(section:string)=>void
}) {
  const analysisJob = jobs.find(j=>j.skill_id==='paper_analysis')
  const result = analysisJob?.status==='succeeded' ? analysisJob.result as Analysis|null : null
  const current = result?.document_hash===documentHash ? result : null
  const pdf = jobs.find(j=>j.skill_id==='analysis_pdf' && (j.result as {analysis_job_id?:string}|null)?.analysis_job_id===analysisJob?.id && j.status==='succeeded')
  const active = jobs.some(j=>!terminalStatus(j.status))
  return <section className="structured-reading"><h3>论文总体分析</h3>
    <p className="muted">按全文分块核对证据，再综合研究问题、方法、创新、实验、局限与研究建议。支持断点恢复；分析和 PDF 排版独立执行。</p>
    <div className="button-row"><button className="button primary" disabled={!available||active||busy==='paper_analysis'} onClick={()=>onRun('paper_analysis','fulltext',true)}>{analysisJob?'检查总体分析任务':'生成总体分析'}</button>
      {current && <><a className="button" href={`/api/jobs/${analysisJob!.id}/artifact`} download>下载分析 Markdown</a>{pdf?<a className="button primary" href={`/api/jobs/${pdf.id}/analysis.pdf`} download>下载总体分析 PDF</a>:<button className="button" disabled={active||busy==='analysis_pdf'} onClick={()=>onRun('analysis_pdf','fulltext',true,analysisJob!.id)}>按翻译模板生成分析 PDF</button>}</>}
    </div>
    {!available && !current && <p className="notice">请在真实模式配置模型，并确认全文解析警告后生成。不会以摘要代替全文分析。</p>}
    {jobs.filter(j=>['paper_analysis','analysis_pdf'].includes(j.skill_id)).filter((j,i,a)=>a.findIndex(x=>x.skill_id===j.skill_id)===i).map(job=><div className="reading-job" key={job.id}><strong>{job.skill_id==='paper_analysis'?'总体分析':'总体分析排版'} · {statusLabel(job.status)}</strong><p>{job.stage} · {job.progress}%</p>{job.error&&<p className="error-text">{job.error}</p>}<div className="button-row">{!terminalStatus(job.status)&&<button className="button" disabled={busy===job.id} onClick={()=>onJobAction(job,'cancel')}>取消</button>}{job.can_retry&&<button className="button" disabled={busy===job.id} onClick={()=>onJobAction(job,'retry')}>从检查点继续{job.requires_confirmation?'（需确认费用）':''}</button>}</div></div>)}
    {current ? <>{headings.map(([key,label])=><section key={key}><h4>{label}</h4>{current[key].map((point,i)=><div key={i}><p><span className="tag neutral">{labels[point.basis]}</span> {point.text}</p>{point.evidence.map(id=>{const evidence=current.evidence_locations.find(e=>e.paragraph_id===id);return evidence?<details key={id}><summary>{id} · 查看原文证据</summary><blockquote>{evidence.quote}</blockquote><button className="text-button" onClick={()=>onEvidence(evidence.section)}>{evidence.location} · 打开原文 →</button></details>:null})}</div>)}</section>)}<p className="notice">{current.unknowns.join('；')}</p><p className="fineprint">PDF 沿用中文翻译的单栏或双栏模板、黑色宋体和标题层级；栏式依据原论文识别。</p></>:<p className="notice">尚无与当前全文版本一致的总体分析。</p>}
    <p className="fineprint">论文：{paper.title} · v{paper.version}</p>
  </section>
}

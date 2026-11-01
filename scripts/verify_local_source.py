"""Read one existing local PDF; verify source recovery in an isolated database, without models."""
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import get_settings
from app import fulltext,jobs,repository as repo
from app.local_papers import store_pdf
from app.worker import process_one
from app.local_source import arxiv_reference


def main():
    original=get_settings().database_path.resolve()
    with sqlite3.connect(original.as_uri()+'?mode=ro',uri=True) as db:
        papers=[json.loads(row[0]) for row in db.execute("SELECT payload FROM papers WHERE mode='live'")]
        paper=next(p for p in papers if p.get('source')=='local')
        data=db.execute('SELECT content FROM fulltext_assets WHERE url=?',(paper['pdf_url'],)).fetchone()[0]
        reference=arxiv_reference(data)
        cached=[]
        for url in [f'https://arxiv.org/pdf/{reference}',f'https://arxiv.org/src/{reference}']:
            row=db.execute('SELECT content FROM fulltext_assets WHERE url=?',(url,)).fetchone()
            if row:cached.append((url,row[0]))
    target=ROOT/'output/local-source-verification';target.mkdir(parents=True,exist_ok=True)
    os.environ['DATABASE_PATH']=str(target/'runtime'/'verification.db')
    os.environ['LLM_API_KEY']='';os.environ['LLM_MODEL']='';get_settings.cache_clear()
    repo.init_db();jobs.init_jobs();fulltext.init_documents()
    for url,blob in cached:fulltext.save_asset(url,blob)
    imported=store_pdf(data,paper['original_filename'])
    if imported['job']['status']=='queued':process_one('verify-local-parse')
    body=jobs.JobInput(paper_id=imported['paper']['id'],skill_id='local_source_fetch',scope='fulltext')
    job=jobs.create_job(body)
    if job['status']=='failed':job=jobs.retry_job(job['id'])
    if job['status']=='queued':process_one('verify-local-source')
    job=jobs.get_job(job['id']);document=fulltext.get_document(imported['paper'])
    report={'job_id':job['id'],'status':job['status'],'error':job.get('error'),'arxiv_reference':reference,
            'method':document['method'],'paragraph_count':document['paragraph_count'],'source_link':document.get('source_link'),
            'warnings':document['warnings'],'original_pdf_unchanged':fulltext.cached_asset(paper['pdf_url'])==data,
            'model_calls':0,'production_data_modified':False}
    (target/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    if job['status']!='succeeded':raise SystemExit(1)

if __name__=='__main__':main()

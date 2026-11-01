"""Explicit real-model acceptance against existing cached papers, isolated from the user's DB.
Run: backend/.venv/Scripts/python.exe scripts/verify_analysis_weekly.py --run-model
No network source search: recorded real metadata/fulltext is replayed; model requests are real.
"""
import argparse,json,os,sys,threading,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.config import get_settings
from app import repository as repo,fulltext,jobs


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-model',action='store_true');parser.add_argument('--resume-analysis',action='store_true');args=parser.parse_args()
    if not args.run_model:parser.error('Explicit --run-model is required')
    settings=get_settings()
    if not settings.llm_api_key:raise RuntimeError('模型未配置')
    paper=repo.get_paper('2606.22995','live');document=fulltext.get_document(paper)
    if not document:raise RuntimeError('Existing real fulltext not available')
    with repo.connect() as db:
        assets=[dict(r) for r in db.execute('SELECT * FROM fulltext_assets WHERE url IN (?,?)',(document['source_url'],f"https://arxiv.org/pdf/{paper['id']}v{paper['version']}"))]
    runtime=ROOT/'tmp'/'analysis-live';runtime.mkdir(parents=True,exist_ok=True)
    output=ROOT/'output'/'analysis-weekly-verification';output.mkdir(parents=True,exist_ok=True)
    os.environ.update(DATABASE_PATH=str(runtime/'test.db'),LLM_API_KEY=settings.llm_api_key,LLM_BASE_URL=settings.llm_base_url,LLM_MODEL=settings.llm_model)
    get_settings.cache_clear()
    from app.main import app
    from app.worker import process_one
    from app import subscriptions as sub
    from app.schemas import Profile
    from fastapi.testclient import TestClient
    stop=threading.Event();owner='analysis-live-verification'
    def pulse():
        while not stop.is_set():jobs.heartbeat(owner);stop.wait(5)
    def save(name,data):
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    def request(client,path,body):
        r=client.post(path,json=body);r.raise_for_status();return r.json()
    def complete(job):
        if job['status']=='queued':process_one(owner)
        done=jobs.get_job(job['id']);print(json.dumps({'skill':done['skill_id'],'status':done['status'],'error':done['error']},ensure_ascii=False),flush=True)
        return done
    with TestClient(app) as client:
        repo.save_profile(Profile(interests=['Agent 强化学习','大模型后训练']))
        repo.upsert_paper(paper);fulltext.save_document(paper,document)
        for asset in assets:fulltext.save_asset(asset['url'],asset['content'])
        thread=threading.Thread(target=pulse,daemon=True);thread.start()
        try:
            job=request(client,'/api/jobs',{'skill_id':'paper_analysis','paper_id':paper['id'],'scope':'fulltext','acknowledge_partial':True})
            if args.resume_analysis and job['status']=='failed' and not job.get('requires_confirmation'):
                job=request(client,'/api/jobs/'+job['id']+'/retry',{})
            done=complete(job);save('analysis-job.json',done)
            if done['status']=='succeeded':
                md=client.get('/api/jobs/'+job['id']+'/artifact');md.raise_for_status();(output/'总体分析.md').write_bytes(md.content)
                pdfjob=complete(request(client,'/api/jobs',{'skill_id':'analysis_pdf','paper_id':paper['id'],'scope':'fulltext','analysis_job_id':job['id']}))
                save('analysis-pdf-job.json',pdfjob)
                if pdfjob['status']=='succeeded':
                    response=client.get('/api/jobs/'+pdfjob['id']+'/analysis.pdf');response.raise_for_status()
                    assert response.content.startswith(b'%PDF')
                    (output/'论文总体分析.pdf').write_bytes(response.content)
                    save('download.json',{'status':response.status_code,'content_type':response.headers['content-type'],'disposition':response.headers['content-disposition'],'bytes':len(response.content)})
            # Use real previously retrieved abstracts and authoritative venue evidence. This is source replay, not a fresh search.
            original=sub.conference_discovery.search
            for track,topic in ([] if args.resume_analysis else [('agent_rl','Agent 强化学习'),('post_training','大模型后训练')]):
                report=json.loads((ROOT/'output'/'stage3-model-verification'/f'{track}-report.json').read_text(encoding='utf-8'))
                sub.conference_discovery.search=lambda *a,report=report:{'papers':report['papers'],'query':report['query'],'fetched_at':report['source_fetched_at'],'cached':True,'warning':'验收复用已有真实来源快照，未重新联网检索'}
                config=request(client,'/api/subscriptions',{'name':topic+'模型增强验收','topic':topic,'model_enabled':True,'model_limit':2,'limit':4,'enabled':False})
                weekly=complete(request(client,f"/api/subscriptions/{config['id']}/run",{})['job']);save(track+'-weekly.json',weekly)
            sub.conference_discovery.search=original
            print('Artifacts: '+str(output),flush=True)
        finally:stop.set();thread.join(timeout=6)

if __name__=='__main__':main()

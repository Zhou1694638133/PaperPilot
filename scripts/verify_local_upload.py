"""Isolated local-upload acceptance; --live-model uses configured model for one small sample."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import get_settings
from app.main import app
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas


def main():
    args=argparse.ArgumentParser()
    args.add_argument('--live-model',action='store_true')
    options=args.parse_args()
    configured=get_settings()
    # Credentials stay in process memory and the worker environment, never in output files.
    provider=(configured.llm_base_url,configured.llm_model,configured.llm_api_key)
    output=ROOT/'output/validation/local-upload'
    output.mkdir(parents=True,exist_ok=True)
    sample=output/'Local Upload Verification Sample.pdf'
    stream=io.BytesIO(); page=canvas.Canvas(stream); text=page.beginText(50,750)
    lines=[
        'Local Upload Verification Sample',
        'This document is a software verification sample, not a published research paper.',
        'An agent learns a policy from feedback while interacting with an environment.',
        'We describe the training procedure and record evidence for each research claim.',
        'The translator must preserve the original meaning and technical terminology.',
        'This sample reports no experimental measurements or conference acceptance.',
    ]
    for line in lines:text.textLine(line)
    page.drawText(text);page.save();data=stream.getvalue();sample.write_bytes(data)
    audit={'sample':'software verification fixture; not a research paper','model_used':False}
    with tempfile.TemporaryDirectory(prefix='local-upload-validation-',dir=ROOT/'data') as folder:
        assert Path(folder).resolve().is_relative_to((ROOT/'data').resolve())
        os.environ['DATABASE_PATH']=str(Path(folder)/'test.db')
        os.environ['LLM_BASE_URL'],os.environ['LLM_MODEL'],os.environ['LLM_API_KEY']=provider
        get_settings.cache_clear()
        def worker():
            completed=subprocess.run([sys.executable,'-m','app.worker','--once'],cwd=ROOT/'backend',env=os.environ.copy(),
                capture_output=True,timeout=240,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if completed.returncode:raise RuntimeError('Isolated worker failed; exit '+str(completed.returncode))
        with TestClient(app) as client:
            response=client.post('/api/local-papers',params={'filename':sample.name},content=data,headers={'Content-Type':'application/pdf'})
            assert response.status_code==201,response.text
            imported=response.json();identifier=imported['paper']['id']
            worker()
            job=client.get('/api/jobs/'+imported['job']['id']).json()
            assert job['status']=='succeeded',job.get('error')
            doc=client.get('/api/fulltext',params={'paper_id':identifier}).json()['document']
            assert doc['blocks'] and doc['status']=='needs_review'
            assert client.get(imported['paper']['pdf_url']).content==data
            audit.update(upload_http=201,parse_status=job['status'],blocks=len(doc['blocks']),original_sha256=hashlib.sha256(data).hexdigest())
            if options.live_model and provider[1] and provider[2]:
                request={'paper_id':identifier,'skill_id':'paper_translator','scope':'fulltext','acknowledge_partial':True}
                response=client.post('/api/jobs',json=request);assert response.status_code==201,response.text
                job_id=response.json()['id'];worker()
                job=client.get('/api/jobs/'+job_id).json();assert job['status']=='succeeded',job.get('error')
                aligned=client.get(f'/api/jobs/{job_id}/alignment').json()
                assert aligned['completed']==aligned['total'] and all(re.search('[\u4e00-\u9fff]',b['translation']) for b in aligned['blocks'])
                assert client.post('/api/jobs',json=request).json()['id']==job_id
                pdf=client.get('/api/reports/pdf',params={'job_id':job_id});md=client.get(f'/api/jobs/{job_id}/artifact')
                assert pdf.status_code==md.status_code==200
                reader=PdfReader(io.BytesIO(pdf.content));assert len(reader.pages)>0
                assert 'Local Upload Verification Sample' in ''.join(p.extract_text() for p in reader.pages)
                (output/'Local Upload Verification Sample翻译版.pdf').write_bytes(pdf.content)
                (output/'Local Upload Verification Sample翻译版.md').write_bytes(md.content)
                events=client.get(f'/api/jobs/{job_id}/events').text
                assert 'succeeded' in events and 'data:' in events
                audit.update(model_used=True,translation_status=job['status'],aligned=aligned['completed'],
                             pdf_http=pdf.status_code,markdown_http=md.status_code,pdf_pages=len(reader.pages),sse_replay=True,deduplicated=True)
            else:audit['translation']='skipped: --live-model not requested or model not configured'
    (output/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

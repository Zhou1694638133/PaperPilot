"""Compile cached source/translation structure without models or database writes."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import jobs, chinese_pdf as c, fulltext
from app.paper_layout import detect_columns


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('job_id')
    args=parser.parse_args()
    record=jobs.job_record(args.job_id)
    snapshot=record['input']['_chinese']
    items=c.parts(snapshot,record['paper'])
    c.preflight_items(items)
    files,blob=c.source_files(snapshot['document'])
    paper=record['paper']
    original=fulltext.cached_asset(f"https://arxiv.org/pdf/{paper['id']}v{paper['version']}")
    columns=detect_columns(files,original)['columns']
    folder=ROOT/'output/validation/chinese-icml-preflight'
    folder.mkdir(parents=True,exist_ok=True)
    body=[]
    for item in items[1:]:
        if item['kind']=='author':continue
        text=item['text']
        if item['kind'] in {'section','subsection','subsubsection'}:
            text='\\'+item['kind']+'{'+text+'}'+item.get('labels','')
        body.append(text)
    raw='\n\n'.join(body)
    c.validate_tex(raw)
    images=c.assets(blob,files,folder,raw)
    authors=next((i['text'] for i in items if i['kind']=='author'),c.escape(' · '.join(paper['authors'])))
    tex=c.preamble(columns)+r'\title{'+items[0]['text']+'}\n'+r'\author{\parbox{0.95\textwidth}{\centering\small '+authors+'}}\n\\date{}\n\\maketitle\n'+c.layout(raw,columns)+'\n\\end{document}\n'
    (folder/'paper.tex').write_text(tex,encoding='utf-8')
    print(f'Preflight: {len(items)} items; {len(images)} images; {columns} columns',flush=True)
    warnings=c.compile_pdf(folder,lambda:None)
    report={'items':len(items),'images':images,'columns':columns,'warnings':warnings,'model_calls':0,'production_changes':False,'note':'Structure validation only; untranslated supplements are intentionally retained in the diagnostic PDF.'}
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()

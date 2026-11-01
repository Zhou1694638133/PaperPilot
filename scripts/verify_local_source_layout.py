"""Compile recovered real source with the app templates; no model or fabricated translation."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ['DATABASE_PATH']=str(ROOT/'output/local-source-verification/runtime/verification.db')
os.environ['LLM_API_KEY']='';os.environ['LLM_MODEL']=''
sys.path.insert(0,str(ROOT/'backend'))
from app import fulltext,chinese_pdf as c,bilingual_pdf as b
from app.local_papers import list_local_papers
from app.paper_layout import detect_columns
from pypdf import PdfReader

def main():
    paper=list_local_papers()['papers'][0];document=fulltext.get_document(paper)
    files,blob=c.source_files(document)
    columns=detect_columns(files,fulltext.cached_asset(paper['pdf_url']))['columns']
    # Original text stands in both cells only to exercise layout. These are NOT translated outputs.
    snapshot={'document':document,'blocks':[{**block,'translation':block['text']} for block in document['blocks']]}
    items=c.parts(snapshot,paper);c.preflight_items(items)
    target=ROOT/'output/local-source-verification';reports=[]
    for bilingual in (False,True):
        folder=target/('bilingual-layout-original' if bilingual else 'chinese-template-original');folder.mkdir(exist_ok=True)
        if bilingual:
            title,authors,body=b.build_body(snapshot,paper,{})
        else:
            title=items[0]['text'];authors=next((i['text'] for i in items if i['kind']=='author'),'')
            body='\n\n'.join('\\'+i['kind']+'{'+i['text']+'}'+i.get('labels','') if i['kind'] in {'section','subsection','subsubsection'} else i['text'] for i in items[1:] if i['kind']!='author')
        if not bilingual:
            c.validate_tex(body)
        images=c.assets(blob,files,folder,body)
        preamble=c.render_preamble(columns,body)
        if bilingual:
            preamble=preamble.replace(r'\begin{document}',b.TABLE_FIT+'\n'+r'\begin{document}\fontsize{12}{20}\selectfont')
        content=c.layout(body,columns)
        if bilingual:content=b.fit_tables(content)
        tex=preamble+r'\title{'+title+'}\n'+r'\author{\parbox{0.95\textwidth}{\centering\small '+authors+'}}\n'+r'\date{}'+'\n'+r'\maketitle'+'\n'+content+'\n'+r'\end{document}'+'\n'
        (folder/'paper.tex').write_text(tex,encoding='utf-8')
        warnings=c.compile_pdf(folder,lambda:None)
        reader=PdfReader(folder/'paper.pdf');fonts=set()
        for page in reader.pages:
            for f in (page.get('/Resources',{}).get('/Font',{}) or {}).values():fonts.add(str(f.get_object().get('/BaseFont','')))
        report={'profile':'bilingual' if bilingual else 'chinese_template','pages':len(reader.pages),'images':images,'columns':columns,'fonts':sorted(fonts),'warnings':warnings,'model_calls':0,'is_translation':False}
        (folder/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        reports.append(report);print(json.dumps({'profile':report['profile'],'pages':len(reader.pages),'images':len(images),'warnings':len(warnings)},ensure_ascii=True),flush=True)
    (target/'layout-verification.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()

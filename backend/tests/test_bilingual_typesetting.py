import pytest
from app.bilingual_pdf import cached_supplement, merge_protected, paragraph_pair
from app import chinese_pdf as c
import hashlib


def test_shared_bilingual_figure_keeps_one_image_and_complete_captions():
    source = r'\begin{figure}\includegraphics{figure/a.pdf}\caption{Accuracy 95.3 on tasks}\label{fig:a}\end{figure}'
    translated = source.replace('Accuracy', '准确率').replace('on tasks','在任务上')
    result = merge_protected(source, translated)
    assert result.count(r'\includegraphics') == 1 and result.count('95.3') == 2
    assert 'Accuracy 95.3 on tasks' in result and '准确率 95.3 在任务上' in result
    assert result.count(r'\label{fig:a}') == 1
    with pytest.raises(ValueError):
        merge_protected(source, translated.replace('95.3','99.3'))


def test_paragraph_pair_keeps_source_and_translation_without_duplicate_labels():
    result = paragraph_pair(r'Source $x$ \label{p:a}', r'译文 $x$ \label{p:a}')
    assert result.index('Source') < result.index('译文')
    assert result.count(r'\label{p:a}') == 1
    assert r'\fontsize{11.5}{20}' in result and r'\fontsize{12}{20}' in result


def test_supplement_cache_never_calls_model():
    item={'kind':'body','id':'caption','text':'Original caption','translate':True}
    assert cached_supplement(item,{}) == 'Original caption'
    digest=hashlib.sha256((c.VERSION+item['text']).encode()).hexdigest()
    assert cached_supplement(item,{('caption',digest):{'translation':'已有图注'}}) == '已有图注'

def test_acknowledgements_crossing_blocks_do_not_cross_language_groups(monkeypatch):
    from app.bilingual_pdf import build_body
    blocks=[{'id':'a','text':r'\begin{ack} Funding.', 'translatable':True},
            {'id':'b','text':r'Interests. \end{ack}', 'translatable':True}]
    items=[{'id':'title','kind':'title','text':'Title','translate':False},
           {'id':'a','kind':'body','text':r'\begin{ack} 资助。','translate':False},
           {'id':'b','kind':'body','text':r'利益声明。 \end{ack}','translate':False}]
    monkeypatch.setattr(c,'source_files',lambda _:({},b''))
    monkeypatch.setattr(c,'parts',lambda *args:items)
    _,_,body=build_body({'document':{},'blocks':blocks},{'title':'Title','authors':[]},{})
    assert body.count('Acknowledgements / 致谢')==1
    assert r'\begin{ack}' not in body and r'\end{ack}' not in body
    assert all(value in body for value in ['Funding.','Interests.','资助。','利益声明。'])

def test_list_boundaries_can_span_formula_checkpoints():
    from app.bilingual_pdf import paragraph_pair
    first=paragraph_pair(r'We have: \begin{itemize}\item First.',r'我们有：\begin{itemize}\item 第一项。')
    last=paragraph_pair(r'\item Second.\end{itemize}',r'\item 第二项。\end{itemize}')
    assert 'itemize' not in first+last and r'\item' not in first+last
    assert all(value in first+last for value in ['First.','Second.','第一项。','第二项。'])


def test_table_adapter_preserves_all_cells_and_surrounding_text():
    from app.bilingual_pdf import fit_tables
    table = r"\begin{tabular}{lr}Original / 译文 & 91.0\%\\\end{tabular}"
    assert fit_tables('Before '+table+' After') == 'Before '+r'\fitbilingualtable{'+table+'} After'
    assert fit_tables('No table $x$') == 'No table $x$'


@pytest.mark.parametrize("closed", [True, False])
def test_prompt_box_spanning_checkpoints_stays_inside_each_language(monkeypatch, closed):
    from app.bilingual_pdf import build_body
    source = [r"\begin{tcolorbox}[title={Task}] Original prompt.",
              r"\begin{equation}x=3\end{equation}",
              r"Original ending." + (r"\end{tcolorbox}" if closed else "")]
    translated = [source[0].replace("Original prompt.", "原文提示。"), source[1],
                  source[2].replace("Original ending.", "原文结尾。")]
    blocks = [{"id":str(i), "text":text, "translatable":i != 1} for i,text in enumerate(source)]
    items = [{"id":str(i), "kind":"body", "text":text, "translate":False} for i,text in enumerate(translated)]
    monkeypatch.setattr(c, "source_files", lambda _:({},b""))
    monkeypatch.setattr(c, "parts", lambda *args:items)
    if not closed:
        with pytest.raises(ValueError, match="提示框"):
            build_body({"document":{}, "blocks":blocks}, {"title":"Title", "authors":[]}, {})
        return
    _,_,body = build_body({"document":{}, "blocks":blocks}, {"title":"Title", "authors":[]}, {})
    assert body.count(r"\bfseries English") == body.count(r"\bfseries 中文") == 1
    assert body.index(r"\end{tcolorbox}") < body.index(r"\bfseries 中文")
    assert body.count(r"\begin{equation}x=3\end{equation}") == 2
    assert all(text in body for text in ["Original prompt.","Original ending.","原文提示。","原文结尾。"])


def test_plain_macro_terms_are_supplemented_without_retranslating_body(monkeypatch):
    from app.bilingual_pdf import build_body
    files = {'main.tex': r"\documentclass{article}\newcommand{\category}{System Design Issues}\newcommand{\name}{MAST}\newcommand{\count}{three}"}
    monkeypatch.setattr(c, 'source_files', lambda _: (files, b''))
    block = {'id':'p1','text':r'We study \category{} in \name{} with \count{} reviewers.',
             'translation':r'我们研究 \category{}，使用 \name{}，由 \count{} 位评审员评估。',
             'location':'main.tex:1','section':'Method','translatable':True}
    snapshot = {'document':{},'blocks':[block]}
    terms = c.macro_supplements(snapshot)
    assert set(terms) == {'category','count'}
    values = {'category':'系统设计问题','count':'三'}
    cache = {(item['id'],hashlib.sha256((c.VERSION+item['text']).encode()).hexdigest()):{'translation':values[name]}
             for name,item in terms.items()}
    _,_,body = build_body(snapshot, {'title':'Title','authors':[]}, cache)
    assert 'We study System Design Issues in MAST with three reviewers.' in body
    assert '我们研究 系统设计问题，使用 MAST，由 三 位评审员评估。' in body
    assert block['translation'].startswith(r'我们研究 \category{}')


def test_macro_supplements_never_translate_math_symbols(monkeypatch):
    files={'main.tex':r"\newcommand{\symbol}{normal}\newcommand{\labelword}{category}"}
    monkeypatch.setattr(c,'source_files',lambda _: (files,b''))
    snapshot={'document':{},'blocks':[{'text':r'$\symbol$ with \labelword{}.'}]}
    assert set(c.macro_supplements(snapshot))=={'labelword'}


def test_review_overlay_is_source_bound_and_does_not_change_checkpoint(monkeypatch):
    item={'id':'p1','kind':'body','text':'原检查点。','translate':False}
    digest=hashlib.sha256((c.VERSION+item['text']).encode()).hexdigest()
    cache={('review-p1',digest):{'translation':'审校译文。','reviewed':True}}
    assert cached_supplement(item,cache)=='审校译文。'
    assert c.supplement({'_pdf_cache':cache},'owner',item)=='审校译文。'
    assert item['text']=='原检查点。'
    assert cached_supplement({**item,'text':'不同原文。'},cache)=='不同原文。'



def test_bilingual_prose_keeps_model_numbers_in_both_languages():
    from app.bilingual_pdf import merge_protected
    source = r'\textbf{GSM-Plus (w/ GPT-4)} & 84.75 $\pm$ 1.94 \\'
    translated = r'\textbf{GSM-Plus（带 GPT-4）} & 84.75 $\pm$ 1.94 \\'
    result = merge_protected(source, translated)
    assert 'GSM-Plus (w/ GPT-4) / GSM-Plus（带 GPT-4）' in result
    assert result.count('84.75') == result.count('1.94') == 1
    assert result.count(r'$\pm$') == 1

import pytest
from app import chinese_pdf as c
from app.pdf_compat import adapt, normalize


def test_icml_metadata_is_converted_without_losing_authors():
    source = [r'\twocolumn[\icmltitle{A paper}', r'\icmlsetsymbol{equal}{*}',
              r'\begin{icmlauthorlist}\icmlauthor{Jane Doe}{a,equal}\icmlauthor{John Roe}{b} $^{*}$\end{icmlauthorlist}',
              r'\icmlaffiliation{a}{First University}\icmlaffiliation{b}{Second University}',
              r'\icmlcorrespondingauthor{John Roe}{john@example.org}',
              r'\icmlkeywords{Machine Learning}\vskip 0.3in]', r'\printAffiliationsAndNotice{}',
              'Actual body.']
    blocks=[{'id':str(i),'text':t} for i,t in enumerate(source)]
    items=[{'id':b['id'],'text':b['text'],'kind':'body','translate':True} for b in blocks]
    output=adapt(items,blocks)
    author=output[0]['text']
    assert r'Jane Doe\textsuperscript{1,*}' in author
    assert r'John Roe\textsuperscript{2,*}' in author
    assert 'First University' in author and 'Second University' in author
    assert 'john@example.org' in author
    assert output[-1]['text']=='Actual body.'
    assert all('icml' not in i['text'] for i in output)
    c.preflight_items(output)


@pytest.mark.parametrize('text', [r'\renewcommand{\input}{abc}',r'\renewcommand{\thefigure}{\input{secret}}',r'\setcounter{unknown}{0}',r'\write18{bad}',r'\csname input\endcsname'])
def test_unsafe_commands_remain_blocked(text):
    with pytest.raises(ValueError):c.validate_tex(text)


def test_numbering_math_tables_and_code_are_preserved():
    for source in [r'\renewcommand{\theequation}{A.\arabic{equation}}\setcounter{equation}{0}',
                   r'\renewcommand{\arraystretch}{1.2}', r'$\mathbf{x}=\alpha\emptyset$']:
        assert normalize(source)==source
        c.validate_tex(source)
    code=r'\begin{lstlisting}'+'\nx = 3 # literal comment\n'+r'\end{lstlisting}'
    result=adapt([{'id':'c','kind':'body','text':code,'translate':True}],[])[0]
    assert not result['translate'] and r'x = 3 \# literal comment' in result['text']
    c.validate_tex(result['text'])
    table=r'\begin{adjustbox}{max width=\textwidth}\begin{tabular}{lr}A&3\\\end{tabular}\end{adjustbox}'
    result=normalize(table)
    assert 'A&3' in result and r'\resizebox{\linewidth}{!}{' in result
    c.validate_tex(result)


def test_preflight_reports_all_failures_at_once():
    with pytest.raises(ValueError) as error:
        c.preflight_items([{'id':'a','text':r'\unknown{one}'},{'id':'b','text':r'\another{two}'}])
    assert 'a：' in str(error.value) and 'b：' in str(error.value)



def test_code_examples_spanning_blocks_are_not_sent_to_model():
    fence="'"*3
    items=[{'id':'a','kind':'body','translate':True,'text':'Explanation '+fence+'python x = 3'},
           {'id':'b','kind':'body','translate':True,'text':'return x '+fence+' More explanation.'}]
    output=adapt(items,[])
    masked=[]
    for item in output:
        value,tokens=c.mask(item['text'],literal_ranges=item['_literal_ranges'])
        assert c.unmask(value,tokens)==item['text']
        masked.append(value)
    assert 'python' not in ''.join(masked) and 'return' not in ''.join(masked)
    assert 'Explanation' in masked[0] and 'More explanation' in masked[1]


def test_source_specific_alias_requires_verified_definition():
    item={'id':'a','kind':'body','translate':True,'text':r'\tcb{hello}'}
    with pytest.raises(ValueError):adapt([item],[],{'source.tex':r'\def\tcb{\input{secret}}'})
    assert r'\textnormal{hello}' == adapt([item],[],{'source.tex':r'\def\tcb{\textcolor{blue}}'})[0]['text']



def test_template_parameters_are_not_translation_fragments():
    for source in [r'\captionsetup{hypcap=false}',r'\setcounter{equation}{0}']:
        value,tokens=c.mask(source)
        assert value=='⟦0⟧'
        assert c.unmask(value,tokens)==source
    source=r'\captionof{table}{Dataset results.}'
    value,tokens=c.mask(source)
    assert 'table' not in value and 'Dataset results.' in value
    assert c.unmask(value.replace('Dataset results.','数据集结果。'),tokens)==r'\captionof{table}{数据集结果。}'

    value,_=c.mask(r'\renewcommand{\thefigure}{A.\arabic{figure}}')
    assert 'figure' not in value



def test_icml_frontmatter_does_not_discard_unrecognized_prose():
    source=r'\twocolumn[\icmltitle{Title}\icmlauthor{Jane}{a}\icmlaffiliation{a}{University} Important source text.]\printAffiliationsAndNotice{}'
    with pytest.raises(ValueError,match='不能静默删除'):
        adapt([{'id':'a','kind':'body','text':source,'translate':True}],[{'id':'a','text':source}])



@pytest.mark.parametrize('rule',[r'\cmidrule(lr){2-7}',r'\cmidrule[0.4pt](l{2pt}r{.5em}){1-3}',r'\cmidrule{2-4}'])
def test_entire_cmidrule_is_protected(rule):
    value,tokens=c.mask(rule)
    assert value=='⟦0⟧' and tokens==[rule]
    assert c.unmask(value,tokens)==rule


def test_recover_cached_table_rules_from_source_without_touching_prose():
    from app.pdf_compat import restore_table_rules
    source=r'\caption{Learning rate}\cmidrule(lr){2-7} A & 12.5'
    translated=r'\caption{学习率}\cmidrule（学习率）{2-7} 甲 & 12.5'
    expected=r'\caption{学习率}\cmidrule(lr){2-7} 甲 & 12.5'
    assert restore_table_rules(source,translated)==expected
    assert restore_table_rules(source,expected)==expected
    for bad in [translated.replace('{2-7}','{2-8}'),translated+r'\cmidrule(lr){2-7}',translated.replace(r'\cmidrule（学习率）{2-7}','')]:
        with pytest.raises(ValueError):restore_table_rules(source,bad)
    with pytest.raises(ValueError):c.validate_tex(translated)


@pytest.mark.parametrize("consumer", ["chinese", "bilingual"])
def test_long_supplements_preserve_line_breaks_and_code_ranges(tmp_path, monkeypatch, consumer):
    from app import bilingual_pdf as b, fulltext, jobs, repository as repo
    from app.config import get_settings
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.db"))
    get_settings.cache_clear()
    try:
        repo.init_db(); jobs.init_jobs(); fulltext.init_documents()
        # A code region spans several legacy line groups, with prose on each side.
        prefix = "Explanation before code. " * 25 + "\n"
        code = ("secret_identifier = unchanged_function(argument) " * 14 + "\n") * 5
        text = prefix + code + "Explanation after code. " * 25
        item = {"id":"long", "kind":"body", "text":text, "translate":True,
                "_literal_ranges":[(len(prefix), len(prefix)+len(code))]}
        children = []
        def capture(child):
            children.append(child)
            return child["text"]
        if consumer == "chinese":
            original = c.supplement
            monkeypatch.setattr(c, "supplement", lambda record, owner, child: capture(child))
            record = {"id":"job", "model":"fixture", "paper":{"authors":[]},
                      "input":{"translation_job_id":"translation", "_provider_url":"https://example.test", "_chinese":{"glossary":{}}}}
            result = original(record, "owner", item)
        else:
            original = b.cached_supplement
            monkeypatch.setattr(b, "cached_supplement", lambda child, cache: capture(child))
            result = original(item, {})
        assert len(children) > 1
        assert result == text  # Joining must not insert paragraph breaks into TeX/code.
        masked_parts = []
        for child in children:
            ranges = child["_literal_ranges"]
            assert all(0 <= left < right <= len(child["text"]) for left, right in ranges)
            masked, tokens = c.mask(child["text"], literal_ranges=ranges)
            assert c.unmask(masked, tokens) == child["text"]
            masked_parts.append(masked)
        visible = "".join(masked_parts)
        assert "secret_identifier" not in visible and "unchanged_function" not in visible
        assert "Explanation before code." in visible and "Explanation after code." in visible
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("style", ["plain", "icml2026"])
def test_numeric_bibliography_retains_author_metadata_for_text_citations(style):
    source = r"\bibliographystyle{" + style + "}"
    normalized = normalize(source)
    assert normalized == r"\bibliographystyle{plainnat}"
    c.validate_tex(normalized)
    with pytest.raises(ValueError):
        c.validate_tex(r"\bibliographystyle{untrusted}")



def test_listing_options_are_not_rendered_as_code():
    source = r'''\begin{lstlisting}[
        basicstyle=\footnotesize\ttfamily,
        columns=flexible, breaklines=true, morekeywords={a[b]}
    ]
def example():
    return "hello_world"
\end{lstlisting}'''
    rendered = normalize(source)
    assert 'basicstyle' not in rendered and 'breaklines' not in rendered
    assert 'def example():' in rendered and r'hello\_world' in rendered
    assert r'\hspace*{2.0em}return' in rendered
    c.validate_tex(rendered)


def test_listing_literal_tex_is_not_normalized():
    from app.chinese_pdf import escape
    code = r'print("\vskip 2cm \textcolor{red}{x} frametitle=a")'
    rendered = normalize('\\begin{lstlisting}\n' + code + '\n\\end{lstlisting}')
    assert escape(code) in rendered
    assert 'vspace' not in rendered
    with pytest.raises(ValueError, match='未闭合'):
        normalize(r'\begin{lstlisting}[basicstyle={broken} code\end{lstlisting}')


def test_literal_listing_fences_do_not_capture_following_heading():
    items = [
        {"id":"code", "kind":"body", "translate":True, "text": "\\begin{lstlisting}\n```python\nprint(1)\n\\end{lstlisting}"},
        {"id":"heading", "kind":"subsection", "translate":True, "text":"Example for reasoning-action mismatch"},
    ]
    result = adapt(items, [])
    assert result[0]["translate"] is False
    assert result[1]["_literal_ranges"] == []

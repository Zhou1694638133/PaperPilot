"""Generate cached bilingual PDF and audit both real translations without translating again."""
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import bilingual_pdf, chinese_pdf as c, jobs
from app.exports import translation_filename
from app.skills import NUMERIC_PATTERN
from pypdf import PdfReader


def numeric_cells(table):
    # Bilingual prose may repeat a model number; quantitative cells must remain identical.
    cells = []
    for cell in re.split(r'&|\\\\', table):
        cell = re.sub(r'\\(?:toprule|midrule|bottomrule)', '', cell)
        remainder = re.sub(NUMERIC_PATTERN, '', cell)
        remainder = re.sub(r'\\(?:pm|%|textbf)|[$\s{}%±+\-]', '', remainder)
        if not remainder and re.search(NUMERIC_PATTERN, cell):
            cells.append(Counter(re.findall(NUMERIC_PATTERN, cell)))
    return cells


def main():
    delivery = ROOT / 'output/local-pdf-delivery'
    ids = json.loads((delivery / 'jobs.json').read_text())
    translation = jobs.job_record(ids['translation_job'])
    chinese = jobs.job_record(ids['chinese_job'])
    assert translation['status'] == chinese['status'] == 'succeeded'
    snapshot = c.translation_snapshot(translation['paper'], translation['id'])
    original = {**snapshot, 'blocks': [{**b, 'translation': b['text'], 'display_translation': b.get('display_text', b['text'])} for b in snapshot['blocks']]}
    original_items = c.parts(original, translation['paper'])
    source = '\n'.join(item['text'] for item in original_items)
    _, blob = c.source_files(snapshot['document'])
    chinese_result = json.loads(chinese['result_json'])
    bilingual = bilingual_pdf.export(translation['id'])
    bilingual_folder = Path(bilingual.path).parent
    bilingual_metadata = json.loads((bilingual_folder / 'metadata.json').read_text())
    expected_images = chinese_result['images']
    image_pattern = r'\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}'
    expected_image_counts = Counter(re.findall(image_pattern, source))
    math_pattern = r'(?<!\\)\$(?!\$)(?:\\.|[^$])*?\$'
    expected_math = Counter(re.findall(math_pattern, source))
    expected_tables = len(re.findall(r'\\begin\{tabular\*?\}', source))
    table_pattern = r'\\begin\{tabular\*?\}[\s\S]*?\\end\{tabular\*?\}'
    source_tables = re.findall(table_pattern, source)
    expected_icons = source.count(r'\faLightbulbO')
    outputs = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        members = {m.name.removeprefix('./'): m for m in archive.getmembers()}
        for label, folder in [('中文版', c.directory(chinese['id'])), ('双语版', bilingual_folder)]:
            tex = (folder / 'paper.tex').read_text(encoding='utf-8')
            assert Counter(re.findall(image_pattern, tex)) == expected_image_counts, 'Image occurrence mismatch'
            for name in expected_images:
                assert (folder / name).read_bytes() == archive.extractfile(members[name]).read(), name
            assert len(re.findall(r'\\begin\{tabular\*?\}', tex)) == expected_tables
            for original_table, rendered_table in zip(source_tables, re.findall(table_pattern, tex), strict=True):
                assert numeric_cells(original_table) == numeric_cells(rendered_table), 'Table numeric cells changed'
            actual_math = Counter(re.findall(math_pattern, tex))
            assert not (expected_math - actual_math), 'Missing or modified source formula'
            assert tex.count(r'\faLightbulbO') >= expected_icons
            pdf = folder / 'paper.pdf'
            reader = PdfReader(pdf)
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
            assert re.search(r'[\u3400-\u9fff]', text)
            assert not re.search(r'basicstyle\s*=|columns\s*=\s*flexible|breaklines\s*=\s*true', text)
            assert not re.search(r'__PP_PROTECTED_|\\(?:begin|end|frac|cite|includegraphics|footnotesize|ttfamily)\b', text)
            assert not any(word in text for word in ['PaperPilot', '翻译检查点'])
            fonts = sorted({str(f.get_object().get('/BaseFont', '')) for page in reader.pages
                            for f in (page.get('/Resources', {}).get('/Font', {}) or {}).values()})
            assert any('SimSun' in f for f in fonts)
            assert any('FontAwesome' in f for f in fonts)
            target = ROOT / 'output/pdf/local-mast' / label
            target.mkdir(parents=True, exist_ok=True)
            destination = target / translation_filename(original_items[0]['text'], 'pdf')
            shutil.copyfile(pdf, destination)
            outputs.append({'variant': label, 'pdf': str(destination), 'source_folder': str(folder),
                            'pages': len(reader.pages), 'bytes': pdf.stat().st_size,
                            'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
                            'images_byte_identical': len(expected_images), 'tables': expected_tables,
                            'inline_formula_occurrences_preserved': sum(expected_math.values()),
                            'lightbulb_icons': tex.count(r'\faLightbulbO'), 'table_numeric_values_unchanged': True, 'fonts': fonts})
    with sqlite3.connect(delivery / 'before-generation.db') as old, jobs.connect() as now:
        old_rows = old.execute('select job_id,paragraph_id,source_hash,translation from translation_chunks').fetchall()
        for jid, pid, sh, tr in old_rows:
            row = now.execute('select source_hash,translation from translation_chunks where job_id=? and paragraph_id=?', (jid,pid)).fetchone()
            assert tuple(row) == (sh,tr)
        chunks = [dict(r) for r in now.execute('select * from translation_chunks where job_id=? order by paragraph_id', (translation['id'],))]
        for saved in json.loads((delivery / 'supplements-before-recompile.json').read_text(encoding='utf-8')):
            row = now.execute('select source_hash,payload from analysis_chunks where job_id=? and chunk_id=?', (chinese['id'],saved['chunk_id'])).fetchone()
            assert tuple(row) == (saved['source_hash'], saved['payload'])
    before = json.loads((delivery / 'translation-before-typeset.json').read_text())
    assert before == {'count': len(chunks), 'sha256': hashlib.sha256(jobs.encode(chunks).encode()).hexdigest()}, 'Typesetting altered translation checkpoints'
    report = {'paper_id': ids['paper_id'], 'translation_job': translation['id'], 'chinese_job': chinese['id'],
              'translation_chunks': len(chunks), 'old_chunks_unchanged': len(old_rows),
              'translation_checkpoint_sha256': hashlib.sha256(jobs.encode(chunks).encode()).hexdigest(),
              'outputs': outputs, 'bilingual_model_calls': bilingual_metadata['model_calls'],
              'chinese_layout_warnings': chinese_result['layout_warnings'],
              'bilingual_layout_warnings': bilingual_metadata['layout_warnings']}
    (delivery / 'final-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()

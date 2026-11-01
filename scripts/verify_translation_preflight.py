"""Read-only regression over cached paper documents; never calls a model or changes checkpoints."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import repository as repo, skills
from app.translation_policy import preflight


def main():
    documents = {}
    with repo.connect() as db:
        for row in db.execute('SELECT payload FROM documents'):
            document = json.loads(row['payload'])
            documents[(document['paper_id'], document['version'], document['method'])] = document
    results = []
    for document in documents.values():
        blocks = document['blocks']
        report = preflight(blocks)
        ranges = skills.code_literal_ranges(blocks)
        kinds = {item['paragraph_id']: item['kind'] for item in report['items']}
        checked, failures = 0, []
        for block in blocks:
            if kinds[block['id']] == 'blocked':
                continue
            try:
                segments, pieces = skills.latex_translation_plan(block['text'], ranges[block['id']])
                output = {'segments': [{'id': item['id'], 'translation': item['text']} for item in segments]}
                assert skills.restore_latex_segments(output, segments, pieces) == block['text']
                checked += 1
            except (ValueError, AssertionError) as exc:
                failures.append({'paragraph_id':block['id'], 'error':str(exc)})
        results.append({'paper_id':document['paper_id'], 'version':document['version'], 'method':document['method'],
                        'document_hash':document['document_hash'], 'counts':report['counts'],
                        'roundtrip_checked':checked, 'roundtrip_failures':failures,
                        'blocked':[item for item in report['items'] if item['kind']=='blocked']})
    target = ROOT / 'output/validation/translation-policy'
    target.mkdir(parents=True, exist_ok=True)
    audit = {'model_calls':0, 'production_changes':False, 'documents':results,
             'note':'结构预检和原文往返验证，不等于全文翻译质量验证。'}
    (target / 'corpus-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'papers':len({r['paper_id'] for r in results}), 'documents':len(results),
                      'checked':sum(r['roundtrip_checked'] for r in results),
                      'failures':sum(len(r['roundtrip_failures']) for r in results),
                      'blocked':sum(len(r['blocked']) for r in results)},ensure_ascii=False))
    if any(r['roundtrip_failures'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

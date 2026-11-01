"""Render completed delivery PDFs for visual review, using the existing Poppler and Pillow."""
import json
from pathlib import Path
import shutil
import subprocess
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

def main():
    report = json.loads((ROOT/'output/local-pdf-delivery/final-audit.json').read_text(encoding='utf-8'))
    renderer = shutil.which('pdftoppm')
    if not renderer:
        raise RuntimeError('Existing pdftoppm installation not found')
    results = []
    for index, item in enumerate(report['outputs']):
        label = 'chinese' if index == 0 else 'bilingual'
        folder = ROOT/'tmp/pdfs/local-mast'/label
        folder.mkdir(parents=True,exist_ok=True)
        expected = (ROOT/'tmp/pdfs/local-mast'/label).resolve()
        for old in folder.glob('page-*.png'):
            if old.resolve().parent != expected:
                raise RuntimeError('Unexpected thumbnail path')
            old.unlink()
        subprocess.run([renderer,'-scale-to','640','-png',item['pdf'],str(folder/'page')],check=True,
                       timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),capture_output=True)
        pages = sorted(folder.glob('page-*.png'),key=lambda p:int(p.stem.split('-')[-1]))
        for start in range(0,len(pages),9):
            sheet=Image.new('RGB',(1200,1665),'#dddddd');draw=ImageDraw.Draw(sheet)
            for offset,path in enumerate(pages[start:start+9]):
                with Image.open(path) as original:
                    image=original.convert('RGB');image.thumbnail((388,520))
                    x=(offset%3)*400+(400-image.width)//2;y=(offset//3)*555+25
                    sheet.paste(image,(x,y));draw.text(((offset%3)*400+12,y-18),f'{label} page {start+offset+1}',fill='black')
            target=ROOT/'output/local-pdf-delivery'/f'{label}-review-{start//9+1}.png'
            sheet.save(target);results.append(str(target))
    (ROOT/'output/local-pdf-delivery/visual-review-files.json').write_text(json.dumps(results),encoding='utf-8')
    print(json.dumps(results),flush=True)

if __name__=='__main__':
    main()

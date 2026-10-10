"""Download four Chinese learning manuals with verified noncommercial licenses."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'eval/chinese-books.json'


def verify(path, book):
    if hashlib.sha256(path.read_bytes()).hexdigest() != book['sha256']:
        raise ValueError(f"{book['title']}: PDF changed; recheck its license before accepting a new version.")
    with pymupdf.open(path) as pdf:
        if len(pdf) != book['pages']:
            raise ValueError('Unexpected physical page count.')
        license_text = ''.join(pdf[book['license_page'] - 1].get_text().split())
        if ''.join(book['license_marker'].split()) not in license_text:
            raise ValueError('Expected license is absent from the PDF copyright page.')
        chinese_chars = sum('\u4e00' <= c <= '\u9fff' for page in pdf for c in page.get_text())
        if chinese_chars < 1000:
            raise ValueError('PDF does not contain sufficient searchable Chinese text.')


def download(book, output):
    target = output / book['pdf_name']
    if target.exists():
        verify(target, book)
    else:
        temporary = target.with_suffix('.pdf.partial')
        try:
            print(f"Downloading {book['title']} from WHO IRIS...", flush=True)
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                with client.stream('GET', book['download_url']) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as handle:
                        for block in response.iter_bytes(1024 * 1024):
                            handle.write(block)
            verify(temporary, book)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    sidecar = target.with_suffix('.json')
    if not sidecar.exists():
        sidecar.write_text(json.dumps({key: book[key] for key in
            ('title', 'author', 'source', 'license', 'source_url')}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Verified {target.name}: {book['pages']} physical pages; {book['license']}", flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.local/chinese-books')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for book in json.loads(MANIFEST.read_text(encoding='utf-8'))['books']:
        download(book, args.output)
    print('Ready. Keep attribution and original notices; these are learning manuals, not undergraduate core textbooks.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Download failed: {exc}', file=sys.stderr)
        sys.exit(1)

"""Download three licensed textbooks from NLM's documented bulk archive service."""
import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path
import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'eval/open-books.json'


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(book, output, cache):
    target = output / book['pdf_name']
    if target.exists() and fingerprint(target) != book['sha256']:
        raise ValueError(f'{target.name} already exists with different content; no files were overwritten.')
    if not target.exists():
        archive_path = cache / (book['id'] + '.tar.gz')
        if not archive_path.exists():
            temporary = archive_path.with_suffix('.partial')
            print(f"Downloading {book['title']} from NLM LitArch...", flush=True)
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                with client.stream('GET', book['archive_url']) as response:
                    response.raise_for_status()
                    total, reported = 0, 0
                    with temporary.open('wb') as handle:
                        for block in response.iter_bytes(1024 * 1024):
                            handle.write(block)
                            total += len(block)
                            if total - reported >= 32 * 1024 * 1024:
                                print(f'  received {total // (1024 * 1024)} MB', flush=True)
                                reported = total
            temporary.replace(archive_path)
        temporary = target.with_suffix('.pdf.partial')
        try:
            # Read one named member; never unpack arbitrary archive paths or links.
            with tarfile.open(archive_path, 'r:gz') as archive:
                member = archive.getmember(book['archive_member'])
                if not member.isfile():
                    raise ValueError('Expected a regular PDF file in the archive.')
                with archive.extractfile(member) as source, temporary.open('wb') as handle:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        handle.write(block)
            if fingerprint(temporary) != book['sha256']:
                raise ValueError(f"{book['title']}: PDF version changed. Recheck the license and expected pages before accepting a new fingerprint.")
            with pymupdf.open(temporary) as pdf:
                if len(pdf) != book['pages']:
                    raise ValueError('Unexpected page count.')
                license_text = '\n'.join(pdf[i].get_text() for i in range(min(8, len(pdf))))
                if 'Creative Commons Attribution 4.0' not in license_text:
                    raise ValueError('Expected license text is absent from the PDF.')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    metadata = target.with_suffix('.json')
    if not metadata.exists():
        metadata.write_text(json.dumps({key: book[key] for key in ('title', 'author', 'source', 'license', 'source_url')}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Verified {target.name}: {book['pages']} pages; {book['license']}", flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.local/acceptance/books')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cache = ROOT / '.local/acceptance'
    cache.mkdir(parents=True, exist_ok=True)
    for book in json.loads(MANIFEST.read_text(encoding='utf-8'))['books']:
        download(book, args.output, cache)
    print('All three books are ready. Keep original copyright notices and exceptions when sharing excerpts.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Download failed: {exc}', file=sys.stderr)
        sys.exit(1)

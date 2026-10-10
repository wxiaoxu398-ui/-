import re
from collections import Counter


def clean_pages(pages: list[str]) -> list[str]:
    # Only repeated short edge lines are removed. Never merge physical PDF pages.
    edges = Counter()
    for text in pages:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in set(lines[:1] + lines[-1:]):
            if len(line) < 80:
                edges[line] += 1
    repeated = {line for line, count in edges.items() if len(pages) >= 5 and count >= max(3, len(pages) * 0.6)}
    result = []
    for text in pages:
        lines = text.splitlines()
        for idx in (0, len(lines) - 1):
            if lines and lines[idx].strip() in repeated:
                lines[idx] = ''
        result.append('\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in lines).strip())
    return result


def split_pages(pages: list[str], target=480, overlap=50):
    section = ''
    for page_number, text in enumerate(clean_pages(pages), 1):
        if not text:
            continue
        heading = re.search(r'^(第.{1,8}[章节].*|\d+\.\d+\s+[^\n]+)', text, flags=re.M)
        if heading:
            section = heading.group(0)[:120]
        start = 0
        while start < len(text):
            end = min(start + target, len(text))
            if end < len(text):
                cut = max(text.rfind('\n', start + target // 2, end), text.rfind('。', start + target // 2, end))
                if cut > start:
                    end = cut + 1
            content = text[start:end].strip()
            if content:
                yield {'content': content, 'page_start': page_number, 'page_end': page_number, 'section': section}
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)

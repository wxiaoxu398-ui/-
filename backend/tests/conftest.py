import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SANDBOX = Path(tempfile.mkdtemp(prefix='medrag-tests-'))
os.environ['DB_PATH'] = str(SANDBOX / 'test.db')
os.environ['BOOKS_DIR'] = str(SANDBOX / 'books')
os.environ['CHROMA_DIR'] = str(SANDBOX / 'chroma')
os.environ['EMBEDDING_BACKEND'] = 'lexical'
os.environ['LLM_API_KEY'] = ''

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

CLAIMS = [
    ('心肌动作电位', '心肌动作电位分为零期、一期、二期、三期、四期。', 112),
    ('肾小球滤过', '测试材料记录肾小球滤过的影响因素为有效滤过压与滤过系数。', 113),
    ('肺表面活性物质', '测试材料记录肺表面活性物质可降低肺泡表面张力。', 114),
    ('氧解离曲线', '测试材料记录温度变化与氧解离曲线有关。', 115),
    ('骨骼肌兴奋收缩耦联', '测试材料记录骨骼肌兴奋收缩耦联与钙离子有关。', 116),
    ('突触传递', '测试材料记录突触传递具有单向性。', 117),
    ('先天免疫', '测试材料记录先天免疫与适应性免疫是两类免疫机制。', 2),
    ('抗体基本结构', '测试材料记录抗体基本结构包含重链与轻链。', 3),
    ('补体系统', '测试材料记录补体系统参与免疫防御。', 4),
    ('肾单位结构', '测试材料记录肾单位结构包含肾小体与肾小管。', 5),
]


def make_pdf(path, page_count, entries):
    pdf = fitz.open()
    for number in range(1, page_count + 1):
        page = pdf.new_page()
        text = entries.get(number, f'用于软件测试的编号记录。记录序号为 {number}。此内容不作为医学教材。')
        page.insert_textbox(fitz.Rect(45, 50, 540, 780), f'测试页 {number}\n{text}', fontname='china-s', fontsize=11)
    pdf.set_metadata({'title': path.stem, 'author': '自动化测试夹具，非教材'})
    pdf.save(path)
    pdf.close()


@pytest.fixture(scope='session')
def client():
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    make_pdf(settings.books_dir / '生理测试.pdf', 500, {p: c for _, c, p in CLAIMS[:6]})
    make_pdf(settings.books_dir / '免疫测试.pdf', 8, {p: c for _, c, p in CLAIMS[6:9]})
    make_pdf(settings.books_dir / '结构测试.pdf', 5, {p: c for _, c, p in CLAIMS[9:]})
    with TestClient(app) as test_client:
        yield test_client

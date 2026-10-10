"""Optional real BGE + Chroma smoke test. Uses isolated temporary PDF fixtures."""
import os
import sys
import tempfile
from pathlib import Path
root = Path(__file__).resolve().parents[1]
temp = Path(tempfile.mkdtemp(prefix='medrag-bge-'))
os.environ['BOOKS_DIR'] = str(temp / 'books')
os.environ['DB_PATH'] = str(temp / 'test.db')
os.environ['CHROMA_DIR'] = str(temp / 'chroma')
os.environ['MODEL_CACHE_DIR'] = str(root / 'backend/app/data/models')
os.environ['EMBEDDING_BACKEND'] = 'bge'
os.environ['LLM_API_KEY'] = ''
sys.path.insert(0, str(root / 'backend'))
import pymupdf
from app.db import init_db
from app.config import settings
from app.rag.ingest import ingest_all, index_lock, state
from app.rag.retrieve import hybrid_search
from app.rag.embedder import collection
init_db()
settings.books_dir.mkdir()
for idx, text in enumerate(['心肌动作电位分为零期、一期、二期、三期、四期。', '先天免疫与适应性免疫是两类免疫机制。', '肾单位结构包含肾小体与肾小管。']):
    doc = pymupdf.open()
    for page in range(500 if idx == 0 else 2):
        p = doc.new_page()
        content = text if page == 111 or idx != 0 else f'用于软件测试的序号记录 {page + 1}，此页不含医学知识。'
        p.insert_textbox(pymupdf.Rect(50, 50, 550, 780), content, fontname='china-s', fontsize=12)
    doc.save(settings.books_dir / f'软件测试夹具{idx}.pdf')
    doc.close()
index_lock.acquire()
ingest_all(True)
assert state['status'] == 'complete', state
assert collection().count() >= 500
results = []
for question in ['心肌动作电位', '先天免疫', '肾单位结构']:
    sources = hybrid_search(question)
    assert sources
    if question == '心肌动作电位': assert sources[0]['page_start'] == 112
    if question == '先天免疫': assert sources[0]['title'] == '软件测试夹具1'
    if question == '肾单位结构': assert sources[0]['title'] == '软件测试夹具2'
    results.append({'question': question, 'top_page': sources[0]['page_start'], 'score': sources[0]['score']})
import json
for question in ['明天的上证指数会涨多少？', '我的账号密码是什么？', '预测下一期彩票中奖号码。']:
    assert not hybrid_search(question), question
print(json.dumps({'backend': 'bge', 'model_dimensions': 512, 'chunks': collection().count(), 'results': results, 'note': 'isolated software fixtures, not textbook answer validation'}, ensure_ascii=False))

import asyncio
import re
from openai import AsyncOpenAI
from ..config import settings
from .prompts import REFUSAL, SYSTEM, LEARNING_INSTRUCTIONS, source_prompt
from .usage import record_usage


def clinical_request(question):
    personal = re.search(r'(我|患者|病人|孩子|宝宝|家人|妈妈|爸爸).{0,24}(症状|疼|痛|发烧|发热|血压|血糖|吃药|咳|胸闷|头晕|怀孕)', question)
    action = re.search(r'(给我|给患者|开个|开一|应该|该|建议|帮我).{0,12}(处方|吃什么药|用什么药|用药|诊断|治疗|剂量)|诊断一下|怎么治疗|服用多少|每天吃几|推荐.*药|帮我判断.*病', question)
    english = (re.search(r'\b(i have|my (child|baby|mother|father))\b.{0,40}\b(pain|fever|cough|symptoms?|rash|dizziness)\b', question, re.I)
               or re.search(r'\b(diagnose me|prescribe|what (medicine|medication|drug) should i|how much .* should i (take|use)|should i take)\b', question, re.I))
    return bool(personal or action or english)


def valid_block(block, source_count):
    if block.strip() == REFUSAL:
        return True
    if re.search(r'根据我的知识|先验知识|作为.*模型|```', block):
        return False
    refs = [int(i) for i in re.findall(r'\[(\d+)\]', block)]
    if not refs or any(i < 1 or i > source_count for i in refs):
        return False
    lines = block.splitlines()
    for index, line in enumerate(lines):
        line = line.strip()
        if not line or re.fullmatch(r'[|:\-\s]+', line):
            continue
        # A Markdown table's header labels are structural, not factual data rows.
        if line.startswith('|') and index + 1 < len(lines) and re.fullmatch(r'[|:\-\s]+', lines[index + 1]):
            continue
        # Inline citations must accompany every sentence, not just the final paragraph.
        sentences = re.split(r'(?<=[。！？!?])\s*|(?<=[A-Za-z\]])\.(?=\s+[A-Za-z]|$)\s*', line)
        for sentence in sentences:
            if not re.search(r'[\w\u4e00-\u9fff]', re.sub(r'\[\d+\]', '', sentence)):
                continue
            if not re.search(r'\[\d+\]', sentence):
                return False
    return True


async def generate(question, sources, history, metrics=None, learning_mode='answer'):
    metrics = metrics if metrics is not None else {}
    metrics['mode'] = 'deepseek' if settings.llm_api_key else 'extractive'
    metrics['learning_mode'] = learning_mode
    metrics['validated_blocks'] = 0
    if not sources or clinical_request(question):
        yield REFUSAL
        return
    if not settings.llm_api_key:
        if not settings.allow_extractive:
            raise RuntimeError('尚未配置 DeepSeek API 密钥。请在设置中填写。')
        # Explicit exact-excerpt mode; no synthesized or inferred medical claims.
        for source in sources[:3]:
            yield f"> {source['content'].replace(chr(10), chr(10) + '> ')}\n\n— 《{source['title']}》PDF 第 {source['page_start']} 页 [{source['index']}]\n\n"
            await asyncio.sleep(0)
        return
    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url,
                         timeout=12, max_retries=1)
    pending = ''
    try:
        async with client, asyncio.timeout(25):
            stream = await client.chat.completions.create(model=settings.llm_model,
                messages=[{'role': 'system', 'content': SYSTEM + '\n' + LEARNING_INSTRUCTIONS[learning_mode]}, *history[-6:],
                          {'role': 'user', 'content': source_prompt(question, sources)}],
                temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens, stream=True,
                stream_options={'include_usage': True}, extra_body={'thinking': {'type': 'disabled'}})
            async with stream:
                async for event in stream:
                    if getattr(event, 'usage', None):
                        record_usage(event.usage, getattr(event, 'model', None) or settings.llm_model, settings.llm_base_url, metrics)
                    if event.choices:
                        choice = event.choices[0]
                        pending += choice.delta.content or ''
                        while '\n\n' in pending:
                            block, pending = pending.split('\n\n', 1)
                            if block.strip():
                                if not valid_block(block, len(sources)):
                                    raise ValueError('模型输出未通过引用编号与逐句引用检查，已停止输出。')
                                metrics['validated_blocks'] += 1
                                yield block + '\n\n'
                        if choice.finish_reason == 'length':
                            raise ValueError('回答达到长度限制，请缩小问题范围后重试。')
                if pending.strip():
                    if not valid_block(pending, len(sources)):
                        raise ValueError('模型输出未通过引用编号与逐句引用检查，已停止输出。')
                    metrics['validated_blocks'] += 1
                    yield pending
    except ValueError:
        raise
    except Exception as exc:
        # Provider exception messages may contain request details; do not expose them.
        raise RuntimeError('DeepSeek 调用失败或超时，请检查密钥、模型名与网络。') from exc

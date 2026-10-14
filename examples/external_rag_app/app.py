"""本机示例 RAG 应用：按 veriscope.e2e.v1 契约接受问题，自己检索、自己生成，返回回答与实际用于生成的资料。

它用来实测「外部 RAG 端到端接入」这条链路，不是第三方系统，也不复用平台的检索：
  - 检索：按「第 X 条」切分资料，用字符二元组重合度打分取前 K 条（平台用的是向量检索）；
  - 生成：调用 OpenAI 兼容接口（默认 DeepSeek），提示词由本应用自己写，温度 0；
  - 只用标准库，密钥只从环境变量读取，不写入日志或响应。

  python examples/external_rag_app/app.py --doc scenarios/deposit_insurance.txt --port 9000

环境变量：EXAMPLE_RAG_API_KEY（默认回退到 OPENAI_API_KEY）、EXAMPLE_RAG_BASE_URL、EXAMPLE_RAG_MODEL。
"""
from __future__ import annotations

import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import urllib.request

SCHEMA = 'veriscope.e2e.v1'
ARTICLE = re.compile(r'^(第[一二三四五六七八九十百]+条)')
REFUSAL = '资料中没有足够信息回答这个问题。'
PROMPT = """你是一个知识库问答助手。只依据下面的资料回答用户问题，不要使用资料以外的知识。
- 资料能回答时，直接给出答案，并说明依据的是第几条。
- 问题中的说法与资料不一致时，先指出不一致之处，再依据资料说明正确内容。
- 资料不足以回答时，只回答：{refusal}

资料：
{contexts}

问题：{question}"""


def load_passages(path: Path) -> list[dict]:
    """按条切分；条文之前的标题和条文之后的来源说明各自成段，不并入条文。"""
    passages, current = [], None
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        match = ARTICLE.match(line)
        if match:
            current = {'location': match.group(1), 'lines': [line]}
            passages.append(current)
        elif current is not None and not line.startswith('来源'):
            current['lines'].append(line)
    return [{'text': '\n'.join(p['lines']), 'location': p['location']} for p in passages]


def bigrams(text: str) -> Counter:
    chars = [c for c in text if not c.isspace() and c not in '，。、；：？！（）「」“”《》']
    return Counter(a + b for a, b in zip(chars, chars[1:]))


def retrieve(question: str, passages: list[dict], top_k: int) -> list[dict]:
    query = bigrams(question)
    scored = []
    for passage in passages:
        grams = bigrams(passage['text'])
        overlap = sum(min(count, grams[gram]) for gram, count in query.items())
        scored.append((overlap / (sum(query.values()) or 1), passage))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{**passage, 'score': round(score, 4)} for score, passage in scored[:top_k] if score > 0]


def generate(question: str, contexts: list[dict]) -> tuple[str, str | None]:
    key = os.environ.get('EXAMPLE_RAG_API_KEY') or os.environ.get('OPENAI_API_KEY')
    if not key:
        raise RuntimeError('未设置 EXAMPLE_RAG_API_KEY')
    base = os.environ.get('EXAMPLE_RAG_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    model = os.environ.get('EXAMPLE_RAG_MODEL', 'deepseek-chat')
    text = '\n\n'.join(c['text'] for c in contexts) or '（没有检索到资料）'
    body = {'model': model, 'temperature': 0,
            'messages': [{'role': 'user', 'content': PROMPT.format(refusal=REFUSAL, contexts=text, question=question)}]}
    request = urllib.request.Request(f'{base}/chat/completions', json.dumps(body).encode('utf-8'),
                                     {'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode('utf-8'))
    return data['choices'][0]['message']['content'].strip(), data.get('model')


def make_handler(passages: list[dict], title: str, top_k: int):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):  # noqa: N802 - http.server naming
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8'))
                item_id, question = body['id'], body['question']
            except (ValueError, KeyError, TypeError):
                return self._send(400, {'error': 'bad request'})
            contexts = retrieve(question, passages, top_k)
            try:
                answer, returned = generate(question, contexts)
            except Exception as error:  # Report the failure; never invent an answer.
                return self._send(200, {'schemaVersion': SCHEMA, 'id': item_id, 'status': 'error',
                                        'error': {'code': type(error).__name__, 'message': '生成失败'}})
            self._send(200, {
                'schemaVersion': SCHEMA, 'id': item_id,
                'status': 'refused' if REFUSAL in answer else 'answered', 'answer': answer,
                'contexts': [{'text': c['text'], 'sourceId': 'deposit_insurance', 'sourceTitle': title,
                              'location': c['location'], 'rank': rank, 'score': c['score']}
                             for rank, c in enumerate(contexts, start=1)],
                'meta': {'retriever': 'char-bigram-overlap', 'topK': top_k, 'model': returned},
            })

        def log_message(self, format, *args):  # Keep stdout to one line per request, no headers.
            print(f'{self.address_string()} {format % args}', flush=True)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--doc', type=Path, required=True)
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--top-k', type=int, default=3)
    args = parser.parse_args()
    passages = load_passages(args.doc)
    title = args.doc.read_text(encoding='utf-8').splitlines()[0].strip()
    print(f'{len(passages)} 段资料，监听 127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(passages, title, args.top_k)).serve_forever()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Compare the PyTorch reranker with its ONNX build: speed and ranking agreement.

Run this before setting RERANK_BACKEND=onnx anywhere:

    pip install "sentence-transformers[onnx]"
    python scripts/bench_reranker.py                 # uses passages from PG_DSN if reachable
    python scripts/bench_reranker.py --threads 2     # mimic Cloud Run's 2 vCPUs

It reports ms per (query, passage) pair for each backend, and for each test
question how many of PyTorch's top 8 passages ONNX also puts in its top 8.
8/8 on most questions means answers will not change. Speed on a laptop is not
speed on Cloud Run; --threads 2 on CPU gets closer.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

QUESTIONS = [
    "How do I apply to the ECE graduate program?",
    "What scholarships are available for ECE students?",
    "Who works on hardware security in ECE?",
    "What are the prerequisites for ECEN 314?",
    "When is the application deadline for the PhD program?",
    "Tell me about research in power electronics",
]


def load_passages(n: int) -> list[str]:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
        import psycopg2
        conn = psycopg2.connect(os.environ["PG_DSN"], connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT text FROM ecen_docs ORDER BY md5(chunk_id) LIMIT %s", (n,))
            rows = [r[0] for r in cur.fetchall()]
        if rows:
            print(f"Using {len(rows)} real passages from the index.")
            return rows
    except Exception as e:  # noqa: BLE001
        print(f"(database not used: {type(e).__name__}; falling back to synthetic passages)")
    base = ("The Department of Electrical and Computer Engineering at Texas A&M offers "
            "graduate programs in several research areas including power systems, "
            "communications, computer engineering, and microelectronics. ")
    return [(base * (3 + i % 5)) + f" Passage {i}." for i in range(n)]


def time_backend(model, pairs_by_q, repeats: int):
    model.predict(pairs_by_q[0][:4])  # warm up
    best = float("inf")
    scores = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        scores = [model.predict(p) for p in pairs_by_q]
        best = min(best, time.perf_counter() - t0)
    n = sum(len(p) for p in pairs_by_q)
    return best / n * 1000, scores


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=35, help="passages per question (prod sends ~35)")
    ap.add_argument("--max-len", type=int, default=int(os.getenv("RERANK_MAX_LEN", "384")))
    ap.add_argument("--threads", type=int, default=int(os.getenv("TORCH_NUM_THREADS", "2")))
    ap.add_argument("--onnx-file", default=os.getenv("RERANK_ONNX_FILE", "onnx/model_quint8_avx2.onnx"))
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    import torch
    from sentence_transformers import CrossEncoder
    torch.set_num_threads(args.threads)
    os.environ["TORCH_NUM_THREADS"] = str(args.threads)

    passages = load_passages(args.pairs)
    pairs_by_q = [[(q, p) for p in passages] for q in QUESTIONS]

    print(f"\n{len(QUESTIONS)} questions x {len(passages)} passages, "
          f"max_length={args.max_len}, threads={args.threads}\n")

    torch_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2",
                               max_length=args.max_len, device="cpu")
    t_ms, t_scores = time_backend(torch_model, pairs_by_q, args.repeats)
    print(f"PyTorch : {t_ms:7.1f} ms/pair   ({t_ms * len(passages) / 1000:.2f}s per question)")

    os.environ["RERANK_BACKEND"] = "onnx"
    os.environ["RERANK_ONNX_FILE"] = args.onnx_file
    os.environ["RERANK_MAX_LEN"] = str(args.max_len)
    import retriever
    try:
        onnx_model = retriever._load_onnx_reranker(args.max_len)
    except Exception as e:  # noqa: BLE001
        print(f"ONNX    : failed to load ({type(e).__name__}: {e})")
        return 1
    o_ms, o_scores = time_backend(onnx_model, pairs_by_q, args.repeats)
    print(f"ONNX    : {o_ms:7.1f} ms/pair   ({o_ms * len(passages) / 1000:.2f}s per question)")
    print(f"Speedup : {t_ms / o_ms:.1f}x\n")

    k = min(8, len(passages))
    worst = k
    for q, ts, os_ in zip(QUESTIONS, t_scores, o_scores):
        top_t = set(sorted(range(len(ts)), key=lambda i: -ts[i])[:k])
        top_o = set(sorted(range(len(os_)), key=lambda i: -os_[i])[:k])
        same_first = max(range(len(ts)), key=lambda i: ts[i]) == max(range(len(os_)), key=lambda i: os_[i])
        overlap = len(top_t & top_o)
        worst = min(worst, overlap)
        print(f"  top-{k} overlap {overlap}/{k}  top-1 {'same' if same_first else 'DIFFERENT'}  {q}")
    print(f"\nWorst top-{k} overlap: {worst}/{k}. "
          + ("Safe to enable." if worst >= k - 1 else
             "Rankings differ noticeably: run the eval suite before enabling."))
    return 0


if __name__ == "__main__":
    sys.exit(main())

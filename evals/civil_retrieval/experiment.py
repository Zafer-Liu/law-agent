"""Run the course's civil-case retrieval experiment against PostgreSQL + pgvector."""

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import unicodedata
from uuid import UUID

import requests


# Isolated execution still imports the shared implementation from this checkout.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legalweb.case_vectors import (DIMENSION, LOCAL_MODEL, MODEL, TEXT_VERSION,
                                  cloud_settings, embed, local_encoder, ranked_cases)
import psycopg2
from psycopg2.extras import execute_values

ISSUE_FILE = Path(__file__).with_name("query_issues.json")
FACT_START = re.compile(r"(?:向本院提出诉讼请求|(?:原告|上诉人)(?:起诉|诉称|称)|(?:本院|一审法院|原审法院)(?:经审理)?(?:查明|认定)(?:事实)?|经审理查明)[:：]?")
FACT_END = re.compile(r"(?:本院|一审法院|原审法院)认为[:：]?|(?:现)?判决如下[:：]")
TOKEN = re.compile(r"[㐀-鿿]+|[a-z]+")
TITLE_SUFFIX = re.compile(r"(?:(?:一审|二审|再审|民事|判决书|裁定书|调解书))+$")

DDL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE SCHEMA IF NOT EXISTS civil_case_lab",
    """CREATE TABLE IF NOT EXISTS civil_case_lab.cases (
        corpus_sha256 text NOT NULL,
        case_no integer NOT NULL,
        case_id uuid NOT NULL,
        title text NOT NULL,
        case_cause text NOT NULL,
        full_text text NOT NULL,
        statements text NOT NULL,
        reasoning text NOT NULL,
        outcome text NOT NULL,
        search_text text NOT NULL,
        PRIMARY KEY (corpus_sha256, case_id),
        UNIQUE (corpus_sha256, case_no)
    )""",
    f"""CREATE TABLE IF NOT EXISTS civil_case_lab.case_embeddings (
        corpus_sha256 text NOT NULL,
        case_id uuid NOT NULL,
        model text NOT NULL,
        text_version text NOT NULL,
        embedding vector({DIMENSION}) NOT NULL,
        PRIMARY KEY (corpus_sha256, case_id, model, text_version),
        FOREIGN KEY (corpus_sha256, case_id)
            REFERENCES civil_case_lab.cases (corpus_sha256, case_id)
    )""",
)


@dataclass(frozen=True)
class Case:
    no: int
    id: str
    title: str
    cause: str
    full: str
    statements: str
    reasoning: str
    outcome: str
    search: str


@dataclass(frozen=True)
class Query:
    no: int
    text: str
    issue_text: str
    cause: str
    excluded: tuple[str, ...]


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def compact(text):
    return normalize(text).replace(" ", "")


def get_cause(title, known_causes):
    for cause in known_causes:
        if re.search(re.escape(cause) + r"(?:一审|二审|再审|民事|申请|裁定|执行|判决|调解|通知|$)", title):
            return cause
    # Avoid treating party names as an unknown cause. Leave those titles unclassified.
    return ""


def read_cases(root, known_causes):
    raw = (root / "civil_database_1k.json").read_bytes()
    rows = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(rows, list) or len(rows) != 1000:
        raise ValueError("civil_database_1k.json must contain 1000 cases")
    cases = []
    for no, row in enumerate(rows, 1):
        title, full, statements, reasoning, outcome = (
            normalize(row[key]) for key in ("TITLE", "QW", "SS", "LY", "JG")
        )
        if not title or not full:
            raise ValueError(f"Case {no} is missing the title or full text")
        cause = get_cause(title, known_causes)
        facts = statements
        if not facts:
            start = FACT_START.search(full)
            if start:
                facts = FACT_END.split(full[start.start():], maxsplit=1)[0]
        # Procedure-only decisions can have no identifiable fact section.
        search = "\n".join(part for part in (f"案由：{cause}" if cause else "", facts or title) if part)
        cases.append(Case(no, str(UUID(row["uniqid"])), title, cause, full, statements, reasoning, outcome, search))
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Duplicate corpus UUIDs")
    return hashlib.sha256(raw).hexdigest(), cases


def title_key(title):
    title = compact(title).removeprefix("原告")
    return TITLE_SUFFIX.sub("", title).replace("诉被告", "与")


def read_queries(root, cases, issues):
    queries = []
    case_keys = [(case.id, title_key(case.title), compact(case.full), compact(case.statements)) for case in cases]
    for path in (root / "query_cases").glob("*.txt"):
        number, title = path.stem.split("-", 1)
        no = int(number)
        cause, issue = issues[str(no)]
        if not title.endswith(cause):
            raise ValueError(f"Query {no:02} does not match the expected cause")
        text = normalize(path.read_text(encoding="utf-8-sig"))
        if not text:
            raise ValueError(f"Query {no:02} is empty")
        key, body = title_key(title), compact(text)
        excluded = tuple(case_id for case_id, candidate, full, statements in case_keys
                         if key == candidate or body == statements or body in full)
        queries.append(Query(no, text, issue, cause, excluded))
    if sorted(query.no for query in queries) != list(range(1, 11)):
        raise ValueError("Expected exactly one file for each query number 1 through 10")
    return sorted(queries, key=lambda query: query.no)


def store_cases(conn, corpus, cases):
    with conn, conn.cursor() as cur:
        for statement in DDL:
            cur.execute(statement)
        execute_values(cur, """INSERT INTO civil_case_lab.cases VALUES %s
            ON CONFLICT (corpus_sha256, case_id) DO UPDATE SET search_text = EXCLUDED.search_text,
            case_cause = EXCLUDED.case_cause
            WHERE (cases.search_text, cases.case_cause) IS DISTINCT FROM
                  (EXCLUDED.search_text, EXCLUDED.case_cause)""", [
            (corpus, c.no, c.id, c.title, c.cause, c.full, c.statements, c.reasoning, c.outcome, c.search)
            for c in cases
        ], page_size=50)
        cur.execute("SELECT count(*) FROM civil_case_lab.cases WHERE corpus_sha256 = %s", (corpus,))
        if cur.fetchone()[0] != len(cases):
            raise RuntimeError("Database row count differs from the source")
        cur.execute("SELECT version(), extversion FROM pg_extension WHERE extname = 'vector'")
        version, extension = cur.fetchone()
    return {"postgresql": version, "pgvector": extension, "case_count": len(cases)}


def tokens(text):
    for word in TOKEN.findall(text.lower()):
        if len(word) == 1 or word.isascii():
            yield word
        else:
            yield from (word[i:i + 2] for i in range(len(word) - 1))


def result(case, score):
    return {"case_no": case.no, "case_id": case.id, "cause": case.cause or None, "score": round(score, 8)}


def bm25_runs(conn, corpus, cases, queries, k):
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT case_id::text, search_text FROM civil_case_lab.cases
                       WHERE corpus_sha256 = %s ORDER BY case_no""", (corpus,))
        documents = {case_id: Counter(tokens(text)) for case_id, text in cur.fetchall()}
    lengths = {case_id: counts.total() for case_id, counts in documents.items()}
    avg_length = sum(lengths.values()) / len(lengths)
    frequencies = Counter()
    for counts in documents.values():
        frequencies.update(counts.keys())
    idf = {term: math.log1p((len(documents) - n + 0.5) / (n + 0.5)) for term, n in frequencies.items()}
    k1, b = 1.5, 0.75

    def rank(query, focused):
        terms = set(tokens(query.issue_text if focused else query.text)) & idf.keys()
        terms = sorted(terms)
        scored = []
        for case in cases:
            if case.id in query.excluded:
                continue
            counts = documents[case.id]
            length_factor = k1 * (1 - b + b * lengths[case.id] / avg_length)
            score = sum(idf[term] * counts[term] * (k1 + 1) / (counts[term] + length_factor)
                        for term in terms if term in counts)
            if focused and case.cause == query.cause:
                score *= 1.5
            if score > 0:
                scored.append((score, case))
        scored.sort(key=lambda hit: (-hit[0], hit[1].no))
        return [result(case, score) for score, case in scored[:k]]

    return {name: {str(q.no): rank(q, focused) for q in queries}
            for name, focused in (("bm25-raw", False), ("bm25-issues", True))}


def vector_run(conn, corpus, cases, queries, k, batch_size, model, encode):
    by_id = {case.id: case for case in cases}
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT case_id::text FROM civil_case_lab.case_embeddings
                       WHERE corpus_sha256 = %s AND model = %s AND text_version = %s""",
                    (corpus, model, TEXT_VERSION))
        cached = {row[0] for row in cur.fetchall()}
    missing = sorted((case for case in cases if case.id not in cached), key=lambda case: len(case.search))
    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        vectors = encode([case.search for case in batch])
        with conn, conn.cursor() as cur:
            execute_values(cur, """INSERT INTO civil_case_lab.case_embeddings VALUES %s
                ON CONFLICT DO NOTHING""", [
                (corpus, case.id, model, TEXT_VERSION, vector) for case, vector in zip(batch, vectors, strict=True)
            ])
        if start == 0 or (start // batch_size + 1) % 10 == 0 or start + batch_size >= len(missing):
            print(f"{model}: embedded {len(cached) + start + len(batch)}/{len(cases)} cases", flush=True)
    query_vectors = []
    for start in range(0, len(queries), batch_size):
        batch = queries[start:start + batch_size]
        query_vectors.extend(encode([f"案由：{query.cause}\n{query.text}" for query in batch]))
    hits = {}
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM civil_case_lab.case_embeddings
                       WHERE corpus_sha256 = %s AND model = %s AND text_version = %s""",
                    (corpus, model, TEXT_VERSION))
        if cur.fetchone()[0] != len(cases):
            raise RuntimeError("Database embedding count differs from the source")
        for query, vector in zip(queries, query_vectors, strict=True):
            rows = ranked_cases(cur, corpus, model, vector, k, query.excluded)
            hits[str(query.no)] = [result(by_id[hit.case_id], 1.0 - hit.distance) for hit in rows]
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--method", choices=("baseline", "qwen", "both", "bge"), default="baseline")
    parser.add_argument("--local-model-dir", type=Path, help="Existing BAAI/bge-m3 snapshot, for --method bge")
    parser.add_argument("--top-k", type=int, default=5, choices=range(1, 21), metavar="1–20")
    parser.add_argument("--batch-size", type=int, default=8, choices=range(1, 21), metavar="1–20")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out must name a new file")
    use_cloud = args.method in {"qwen", "both"}
    use_local = args.method == "bge"
    if use_local and not (args.local_model_dir and args.local_model_dir.is_dir()):
        parser.error("--method bge requires --local-model-dir pointing to a cached BAAI/bge-m3 snapshot")
    endpoint, key = cloud_settings() if use_cloud else (None, None)
    embedding_model = MODEL if use_cloud else LOCAL_MODEL if use_local else None
    issues = json.loads(ISSUE_FILE.read_text(encoding="utf-8"))
    known_causes = sorted({cause for cause, _ in issues.values()}, key=lambda cause: (-len(cause), cause))
    corpus, cases = read_cases(args.data, known_causes)
    queries = read_queries(args.data, cases, issues)
    dsn = os.environ.get("CASE_VECTOR_DSN")
    if not dsn:
        parser.error("Set CASE_VECTOR_DSN to a dedicated PostgreSQL database")
    with closing(psycopg2.connect(dsn, connect_timeout=8)) as conn:
        db = store_cases(conn, corpus, cases)
        runs = bm25_runs(conn, corpus, cases, queries, args.top_k) if args.method != "qwen" else {}
        if use_cloud:
            with requests.Session() as session:
                runs[MODEL] = vector_run(conn, corpus, cases, queries, args.top_k, args.batch_size,
                                         MODEL, lambda texts: embed(session, endpoint, key, texts))
        elif use_local:
            runs[LOCAL_MODEL] = vector_run(conn, corpus, cases, queries, args.top_k, args.batch_size,
                                           LOCAL_MODEL, local_encoder(args.local_model_dir.resolve()))
    payload = {"corpus_sha256": corpus, "text_version": TEXT_VERSION, "database": db,
               "top_k": args.top_k,
               "queries": {str(q.no): {"cause": q.cause, "excluded_case_ids": q.excluded,
                                        "excluded_case_nos": [c.no for c in cases if c.id in q.excluded]}
                           for q in queries},
               "embedding_model": embedding_model,
               "embedding_dimensions": DIMENSION if embedding_model else None,
               "runs": runs}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(f"Stored {len(cases)} cases and ran {len(queries)} queries: {args.out}")


if __name__ == "__main__":
    main()

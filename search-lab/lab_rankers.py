#!/usr/bin/env python3
"""S2 rankers: the current Hallbook ranker and a BM25F-style replacement.

Pure Python, stdlib only, deterministic and offline. The index is built once from the
bundled skill metadata plus each skill body (headings and first 1,500 characters).
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import common

common.use_clean_source()

from aletheore import skill_hub  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
INTENTS = DATA / "intents.json"

FIELDS = ("name", "title", "description", "intents", "headings", "body", "category")
STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from had has have how i if in into is it its "
    "me my of on or our should so some that the their them then there these this to up us use using "
    "want was we were what when where which while who why will with you your".split()
)
WORD = re.compile(r"[a-z0-9]+")
TOKEN_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")

# Longest suffixes first. Each folds a family of forms to one pseudo-stem.
_SUFFIXES = (
    ("ization", "iz"),
    ("isation", "iz"),
    ("ational", "at"),
    ("ations", "at"),
    ("ation", "at"),
    ("ition", "it"),
    ("tion", "t"),
    ("ments", ""),
    ("ment", ""),
    ("ings", ""),
    ("ing", ""),
    ("edly", ""),
    ("ies", "y"),
    ("sses", "ss"),
    ("ize", "iz"),
    ("ise", "iz"),
    ("en", ""),
    ("ed", ""),
    ("ly", ""),
)

# Only used when the DEV experiment says they help.
SYNONYMS = {
    "bug": "defect",
    "error": "defect",
    "slow": "performance",
    "slowness": "performance",
    "latency": "performance",
    "deploy": "release",
    "ship": "release",
    "releas": "release",
}

DEFAULT_WEIGHTS = {
    "name": 3.0,
    "title": 1.08,
    "description": 2.94,
    "intents": 4.9,
    "headings": 0.6,
    "body": 0.7,
    "category": 2.1,
    "coverage": 3.92,
    "name_hit": 2.8,
    "name_cov": 2.0,
    "phrase": 1.5,
    "prefix": 0.6,
    "quality": 0.05,
    "k1": 1.2,
    "b": 0.75,
}
FIELD_WEIGHT_KEYS = ("name", "title", "description", "intents", "headings", "body", "category")
TUNE_KEYS = FIELD_WEIGHT_KEYS + ("coverage", "name_hit", "phrase", "quality")


def stem(token: str) -> str:
    word = token.lower()
    if len(word) <= 3:
        return word
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + replacement
            break
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "lsz":
        word = word[:-1]
    return word


def tokenize(text: str, use_synonyms: bool = False) -> list[str]:
    tokens: list[str] = []
    for raw in WORD.findall(text.lower()):
        if len(raw) < 2 or raw in STOPWORDS:
            continue
        token = stem(raw)
        if use_synonyms:
            token = SYNONYMS.get(token, token)
        tokens.append(token)
    return tokens


def _name_text(skill_id: str) -> str:
    return skill_id.split("/", 1)[-1].replace("-", " ").replace("_", " ")


class SkillIndex:
    """Tokenised fields for every bundled skill, plus the inverted index used for scoring."""

    def __init__(self, skills_dir: Path | None = None, intents_path: Path | None = None,
                 use_synonyms: bool = False) -> None:
        self.skills_dir = skills_dir
        self.intents_path = intents_path or INTENTS
        self.use_synonyms = use_synonyms
        self.loaded = False

    def ensure_loaded(self) -> "SkillIndex":
        if not self.loaded:
            self.load()
        return self

    def load(self) -> "SkillIndex":
        started = time.perf_counter()
        metas = skill_hub.list_bundled_skills()
        try:
            intents = json.loads(self.intents_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            intents = {}
        self.ids: list[str] = []
        self.doc_tf: list[dict[str, Counter]] = []
        self.doc_len: list[dict[str, int]] = []
        self.doc_bigrams: list[set[tuple[str, str]]] = []
        self.doc_vocab: list[set[str]] = []
        self.quality: list[int] = []
        self.categories: list[str] = []
        df: Counter = Counter()
        for meta in metas:
            skill = skill_hub.get_bundled_skill(meta.skill_id)
            body = skill.content if skill is not None else ""
            headings = "\n".join(line for line in body.splitlines() if line.lstrip().startswith("#"))
            texts = {
                "name": _name_text(meta.skill_id),
                "title": meta.title,
                "description": meta.description,
                "intents": " ".join(intents.get(meta.skill_id) or []),
                "headings": headings,
                "body": body[:1500],
                "category": meta.category.replace("-", " "),
            }
            tf: dict[str, Counter] = {}
            lengths: dict[str, int] = {}
            bigrams: set[tuple[str, str]] = set()
            vocab: set[str] = set()
            for field, text in texts.items():
                tokens = tokenize(text, self.use_synonyms)
                tf[field] = Counter(tokens)
                lengths[field] = len(tokens)
                vocab.update(tokens)
                for left, right in zip(tokens, tokens[1:]):
                    bigrams.add((left, right))
            self.ids.append(meta.skill_id)
            self.doc_tf.append(tf)
            self.doc_len.append(lengths)
            self.doc_bigrams.append(bigrams)
            self.doc_vocab.append(vocab)
            self.quality.append(meta.quality)
            self.categories.append(meta.category)
            df.update(vocab)
        self.n = len(self.ids)
        self.idf = {token: math.log(1.0 + self.n / (1.0 + count)) for token, count in df.items()}
        self.avg_len = {
            field: (sum(lengths[field] for lengths in self.doc_len) / self.n if self.n else 0.0)
            for field in FIELDS
        }
        self.postings: dict[str, dict[int, tuple[float, ...]]] = defaultdict(dict)
        self.prefix_tokens: dict[str, set[str]] = defaultdict(set)
        for di, tf in enumerate(self.doc_tf):
            for token in self.doc_vocab[di]:
                self.postings[token][di] = tuple(float(tf[field].get(token, 0)) for field in FIELDS)
                if len(token) >= 5:
                    self.prefix_tokens[token[:5]].add(token)
        self.postings = dict(self.postings)
        self.loaded = True
        self.load_s = time.perf_counter() - started
        return self


_OLD_STOPWORDS = frozenset(
    "a an and are as at be by do does for from how i if in into is it its me my of on or our so that the "
    "their then there these this to up use using want we what when where which while with you your".split()
)


def _old_stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and not token.endswith("ss") and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) > 3:
        token = token[:-1]
    if len(token) > 3 and token[-1] == token[-2] and token[-1] not in "lsz":
        token = token[:-1]
    return token


def _old_tokens(text: str) -> list[str]:
    return [_old_stem(t) for t in WORD.findall(text.lower())
            if len(t) > 1 and t not in _OLD_STOPWORDS]


class BaselineRanker:
    """The pre-S2 production ranker, reproduced here because skill_hub.py now holds the new one."""

    label = "baseline"

    def __init__(self) -> None:
        self._corpus: tuple[list[tuple], dict[str, float]] | None = None

    def _load(self) -> tuple[list[tuple], dict[str, float]]:
        if self._corpus is None:
            docs = []
            document_frequency: Counter = Counter()
            for meta in skill_hub.list_bundled_skills():
                name_tokens = _old_tokens(meta.skill_id.split("/", 1)[1].replace("-", " ").replace("_", " "))
                category_tokens = set(_old_tokens(meta.category.replace("-", " ")))
                text_tokens = set(_old_tokens(f"{meta.title} {meta.description}"))
                docs.append((meta, name_tokens, category_tokens, text_tokens))
                document_frequency.update(set(name_tokens) | category_tokens | text_tokens)
            total = len(docs)
            idf = {word: math.log(1 + total / (1 + count))
                   for word, count in document_frequency.items()}
            self._corpus = (docs, idf)
        return self._corpus

    def rank(self, query: str, limit: int = 10) -> list[str]:
        query_tokens = list(dict.fromkeys(_old_tokens(query)))
        if not query_tokens or limit <= 0:
            return []
        docs, idf = self._load()
        scored: list[tuple[float, str]] = []
        for meta, name_tokens, category_tokens, text_tokens in docs:
            score = 0.0
            for token in query_tokens:
                weight = idf.get(token, 0.0)
                if token in name_tokens:
                    score += 5 * weight
                elif any(token in name_token for name_token in name_tokens):
                    score += 3 * weight
                if token in category_tokens:
                    score += 2 * weight
                if token in text_tokens:
                    score += 1 * weight
            if score:
                scored.append((score + 0.3 * meta.quality, meta.skill_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [skill_id for _, skill_id in scored[:limit]]


class LabRanker:
    label = "lab"

    def __init__(self, index: SkillIndex | None = None, weights: dict | None = None) -> None:
        weights = weights or {}
        if index is None:
            index = SkillIndex(use_synonyms=bool(weights.get("use_synonyms")))
        self.index = index.ensure_loaded()
        self.weights = {**DEFAULT_WEIGHTS, **weights}
        self._norms: list[list[float]] = []
        b = self.weights["b"]
        for lengths in self.index.doc_len:
            self._norms.append([
                1.0 - b + b * (lengths[field] / self.index.avg_len[field] if self.index.avg_len[field] else 1.0)
                for field in FIELDS
            ])

    def rank(self, query: str, limit: int = 10) -> list[str]:
        return [skill_id for _, skill_id in self.rank_scored(query, limit)]

    def rank_scored(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        if limit <= 0:
            return []
        index = self.index
        query_tokens = list(dict.fromkeys(tokenize(query, index.use_synonyms)))
        if not query_tokens:
            return []
        weights = self.weights
        field_weights = [weights[field] for field in FIELDS]
        k1 = weights["k1"]
        idf = index.idf

        term_maps: list[tuple[str, dict[int, list[float]]]] = []
        candidates: set[int] = set()
        for token in query_tokens:
            merged: dict[int, list[float]] = {}
            exact = index.postings.get(token)
            if exact:
                for di, tfs in exact.items():
                    merged[di] = list(tfs)
            if len(token) >= 5:
                for other in index.prefix_tokens.get(token[:5], ()):
                    if other == token or not other.startswith(token):
                        continue
                    for di, tfs in index.postings[other].items():
                        current = merged.get(di)
                        if current is None:
                            current = merged[di] = [0.0] * len(FIELDS)
                        for j, value in enumerate(tfs):
                            current[j] += weights["prefix"] * value
            if merged:
                candidates.update(merged)
            term_maps.append((token, merged))

        if not candidates:
            return []

        query_pairs = list(zip(query_tokens, query_tokens[1:]))
        n_terms = len(query_tokens)
        scored: list[tuple[float, str]] = []
        for di in candidates:
            score = 0.0
            matched = 0
            norms = self._norms[di]
            for token, merged in term_maps:
                tfs = merged.get(di)
                if not tfs:
                    continue
                matched += 1
                weighted_tf = 0.0
                for j, value in enumerate(tfs):
                    if value:
                        weighted_tf += field_weights[j] * value / norms[j]
                if weighted_tf:
                    score += idf.get(token, 0.0) * weighted_tf * (k1 + 1.0) / (k1 + weighted_tf)
            score += weights["coverage"] * (matched / n_terms)
            name_tf = index.doc_tf[di]["name"]
            name_hits = [token for token in query_tokens if name_tf.get(token)]
            if name_hits:
                score += weights["name_hit"] * sum(idf.get(token, 0.0) for token in name_hits)
                name_tokens = index.doc_vocab[di] & set(name_tf)
                if name_tokens:
                    score += weights["name_cov"] * (len(set(name_hits)) / len(name_tokens))
            if query_pairs:
                bigrams = index.doc_bigrams[di]
                for left, right in query_pairs:
                    if (left, right) in bigrams:
                        score += weights["phrase"] * (idf.get(left, 0.0) + idf.get(right, 0.0)) / 2.0
            score += weights["quality"] * index.quality[di]
            scored.append((score, index.ids[di]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[:limit]


# ---------------------------------------------------------------- tuning

def _objective(result: dict) -> float:
    return 0.5 * result["strict"][1] + 0.3 * result["strict"][5] + 0.2 * result["strict_mrr"]


def _coordinate_ascent(index: SkillIndex, rows: list[dict], evaluate_fn, weights: dict,
                       multipliers: tuple[float, ...] = (0.6, 1.4), passes: int = 2) -> dict:
    def score(candidate: dict) -> float:
        return _objective(evaluate_fn(LabRanker(index=index, weights=candidate), rows, label="tune"))

    best = score(weights)
    print(f"  start objective {best:.4f}", flush=True)
    for pass_no in range(1, passes + 1):
        changed = False
        for key in TUNE_KEYS:
            base = weights[key]
            best_value = base
            for multiplier in multipliers:
                if multiplier == 1.0:
                    continue
                trial = dict(weights)
                trial[key] = base * multiplier
                value = score(trial)
                if value > best + 1e-9:
                    best = value
                    best_value = trial[key]
                    changed = True
                    print(f"  pass {pass_no} {key}: {base:.3f} -> {trial[key]:.3f}  objective {value:.4f}", flush=True)
            weights[key] = best_value
        if not changed:
            break
    return weights


def tune(rows: list[dict], evaluate_fn, passes: int = 2) -> dict:
    """Coordinate ascent on DEV only. evaluate_fn(ranker, rows, label=...) -> metrics dict."""
    best_weights: dict | None = None
    best_score = -1.0
    for use_synonyms in (False, True):
        print(f"\nsynonym folds: {'on' if use_synonyms else 'off'}", flush=True)
        index = SkillIndex(use_synonyms=use_synonyms).load()
        weights = _coordinate_ascent(index, rows, evaluate_fn, dict(DEFAULT_WEIGHTS), passes=passes)
        result = evaluate_fn(LabRanker(index=index, weights=weights), rows, label="dev")
        value = _objective(result)
        print(f"  final dev objective {value:.4f} (R@1 {result['strict'][1]:.3f}, "
              f"R@5 {result['strict'][5]:.3f}, MRR {result['strict_mrr']:.3f})", flush=True)
        if value > best_score:
            best_score = value
            best_weights = dict(weights)
            best_weights["use_synonyms"] = use_synonyms
    return best_weights


def main() -> None:
    if "--build" in sys.argv:
        index = SkillIndex().load()
        print(f"{index.n} skills, load {index.load_s:.2f}s, {len(index.postings)} terms, "
              f"{len(index.prefix_tokens)} prefixes")


if __name__ == "__main__":
    main()

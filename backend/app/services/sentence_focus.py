"""Group reading-order words into sentences, then rank short passages locally."""
import math
import re
from collections import Counter

from .focus_rules import STOP_WORDS, normalized_word


def passages(pages):
    sentences, current = [], []
    previous = None
    for page in pages:
        number = page["page"]
        if previous is not None and number != previous + 1:
            if current:
                sentences.append(current)
                current = []
            if sentences:
                yield sentences
                sentences = []
        previous = number
        block_starts = set(page.get("blockStartWordIds", []))
        for word in page["words"]:
            if current and word["id"] in block_starts:
                sentences.append(current)
                current = []
                if len(sentences) == 8:
                    yield sentences
                    sentences = []
            text = word["text"]
            current.append((number, word))
            end = re.search(r'[.!?。！？][”’"\')\]]*$', text)
            abbreviation = re.fullmatch(r'(?:Mr|Mrs|Ms|Dr|Prof|St|e\.g|i\.e|[A-Z])\.', text)
            if end and not abbreviation:
                sentences.append(current)
                current = []
            if len(sentences) == 8:
                yield sentences
                sentences = []
        if len(sentences) >= 4 and not current:
            yield sentences
            sentences = []
    if current:
        sentences.append(current)
    if sentences:
        yield sentences


def local_ranking(sentences):
    terms = [{normalized_word(word) for _, word in s} - STOP_WORDS - {''} for s in sentences]
    frequency = Counter(term for group in terms for term in group)
    scores = []
    for i, sentence in enumerate(sentences):
        overlap = sum(min(3, frequency[t] - 1) for t in terms[i]) / math.sqrt(max(1, len(terms[i])))
        text = ' '.join(word['text'] for _, word in sentence)
        claim = bool(re.search(r'\b(because|therefore|means|creates|requires|explains|cannot|depends)\b', text, re.I))
        scores.append(overlap + claim + (0.65 if '?' in text else 0) + (0.45 if i == 0 else 0))
    order = sorted(range(len(sentences)), key=scores.__getitem__, reverse=True)
    return set(order[:max(1, round(len(order) * .32))])


def add_sentence(result, sentence):
    for page, word in sentence:
        result[page]["essential"].append(int(word["id"]))


def local_layers(pages):
    result = {p["page"]: {"essential": [], "context": []} for p in pages}
    for group in passages(pages):
        for index in sorted(local_ranking(group)):
            add_sentence(result, group[index])
    return result

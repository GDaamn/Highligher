"""Group reading-order words into sentences, then rank short passages locally."""
import math
import re
from collections import Counter

from .focus_rules import STOP_WORDS, normalized_word


MINIMUM_SUMMARY_WORD_RATIO = 0.5
LOCAL_SUMMARY_SENTENCE_RATIO = 0.5


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


def ranked_sentence_indices(sentences):
    terms = [{normalized_word(word) for _, word in s} - STOP_WORDS - {''} for s in sentences]
    frequency = Counter(term for group in terms for term in group)
    scores = []
    for i, sentence in enumerate(sentences):
        overlap = sum(min(3, frequency[t] - 1) for t in terms[i]) / math.sqrt(max(1, len(terms[i])))
        text = ' '.join(word['text'] for _, word in sentence)
        claim = bool(re.search(r'\b(because|therefore|means|creates|requires|explains|cannot|depends)\b', text, re.I))
        scores.append(overlap + claim + (0.65 if '?' in text else 0) + (0.45 if i == 0 else 0))
    return sorted(range(len(sentences)), key=scores.__getitem__, reverse=True)


def local_ranking(sentences):
    order = ranked_sentence_indices(sentences)
    count = max(1, math.ceil(len(order) * LOCAL_SUMMARY_SENTENCE_RATIO))
    return set(order[:count])


def add_sentence(result, sentence):
    for page, word in sentence:
        result[page]["essential"].append(int(word["id"]))


def local_layers(pages):
    result = {p["page"]: {"essential": [], "context": []} for p in pages}
    for group in passages(pages):
        for index in sorted(local_ranking(group)):
            add_sentence(result, group[index])
    return result


def page_summary_word_ids(page, proposed=None):
    """Return complete summary sentences covering at least half of a reading page."""
    ordered_ids = [int(word["id"]) for word in page["words"]]
    valid_ids = set(ordered_ids)
    proposed = proposed or {}
    selected = {
        int(word_id)
        for word_id in proposed.get("essential", [])
        if int(word_id) in valid_ids
    }
    minimum = math.ceil(len(ordered_ids) * MINIMUM_SUMMARY_WORD_RATIO)
    if len(selected) < minimum:
        fallback = local_layers([page]).get(page["page"], {})
        selected.update(int(word_id) for word_id in fallback.get("essential", []))
    if len(selected) < minimum:
        for group in passages([page]):
            for index in ranked_sentence_indices(group):
                selected.update(
                    int(word["id"])
                    for page_number, word in group[index]
                    if page_number == page["page"]
                )
                if len(selected) >= minimum:
                    break
            if len(selected) >= minimum:
                break
    return [word_id for word_id in ordered_ids if word_id in selected]

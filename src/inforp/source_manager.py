from __future__ import annotations

from collections import defaultdict

from inforp.schemas import Confidence, EventCandidate, SourceProfile, SourceScore


def compute_source_scores(
    sources: list[SourceProfile],
    events: list[EventCandidate],
    existing: dict[str, SourceScore] | None = None,
) -> list[SourceScore]:
    existing = existing or {}
    by_source = {source.id: source for source in sources}
    mentions: dict[str, list[EventCandidate]] = defaultdict(list)

    for event in events:
        for source_id in event.source_ids:
            mentions[source_id].append(event)

    scores: list[SourceScore] = []
    for source_id, source in by_source.items():
        current = existing.get(source_id) or SourceScore(
            source_id=source_id,
            base_reliability=source.reliability_score,
            score=source.reliability_score,
        )
        seen = mentions.get(source_id, [])
        if seen:
            total_importance = sum(event.importance_score for event in seen)
            prior_weight = max(current.mentions, 0)
            new_mentions = current.mentions + len(seen)
            current.avg_importance = (
                (current.avg_importance * prior_weight + total_importance) / max(new_mentions, 1)
            )
            current.mentions = new_mentions
            current.confirmed += sum(1 for event in seen if event.confidence == Confidence.HIGH)
        current.base_reliability = source.reliability_score
        current.score = calculate_score(current)
        scores.append(current)

    return scores


def calculate_score(score: SourceScore) -> float:
    mentions = max(score.mentions, 1)
    confirmed_rate = score.confirmed / mentions
    correction_rate = score.corrected / mentions
    false_rate = score.false_reports / mentions
    value = (
        score.base_reliability
        + confirmed_rate * 0.25
        + score.avg_importance * 0.10
        - false_rate * 0.35
        - correction_rate * 0.15
    )
    return max(0.0, min(1.0, value))

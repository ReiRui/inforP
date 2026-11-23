from inforp.schemas import Confidence, EventCandidate, TopicType
from inforp.verification import apply_confidence_policy, rank_events


def test_ai_low_confidence_is_filtered():
    events = [
        EventCandidate(
            title="AI rumor",
            summary="unverified",
            topic=TopicType.AI_TECH,
            confidence=Confidence.LOW,
        ),
        EventCandidate(
            title="Policy watch",
            summary="unverified but important",
            topic=TopicType.GLOBAL_POLITICS,
            confidence=Confidence.LOW,
        ),
    ]

    accepted, filtered = apply_confidence_policy(events)

    assert filtered == 1
    assert len(accepted) == 1
    assert accepted[0].is_watchlist is True


def test_rank_events_prefers_importance_and_confidence():
    low = EventCandidate(title="low", summary="x", importance_score=0.6, confidence=Confidence.LOW)
    high = EventCandidate(title="high", summary="x", importance_score=0.6, confidence=Confidence.HIGH)

    ranked = rank_events([low, high])

    assert ranked[0].title == "high"


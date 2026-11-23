from __future__ import annotations

from inforp.schemas import Confidence, EventCandidate, TopicType
from urllib.parse import urlparse


AI_TOPICS = {
    TopicType.AI_TECH,
    TopicType.AI_HARDWARE,
    TopicType.AI_INDUSTRY_POLICY,
    TopicType.GENERAL_AI,
}

TIER_RANK = {"T1": 0, "T1_5": 1, "T2": 2}
TIER_LABEL = {"T1": "官方一手", "T1_5": "准官方/研究", "T2": "媒体线索"}


def is_ai_topic(topic: TopicType) -> bool:
    return topic in AI_TOPICS


def normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return None
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def independent_sources(event: EventCandidate) -> int:
    """Count distinct publishing domains, not URLs.

    Five articles syndicating one press release are one source, not five.
    """
    domains = {normalize_domain(url) for url in event.source_urls}
    return len({d for d in domains if d})


def best_tier(event: EventCandidate, tiers_by_domain: dict[str, str] | None) -> str | None:
    if not tiers_by_domain:
        return None
    tiers = []
    for url in event.source_urls:
        domain = normalize_domain(url)
        if not domain:
            continue
        tier = tiers_by_domain.get(domain)
        if tier is None:
            for known, known_tier in tiers_by_domain.items():
                if domain == known or domain.endswith("." + known):
                    tier = known_tier
                    break
        if tier in TIER_RANK:
            tiers.append(tier)
    if not tiers:
        return None
    return min(tiers, key=lambda t: TIER_RANK[t])


def attach_source_signals(
    events: list[EventCandidate],
    tiers_by_domain: dict[str, str] | None,
) -> list[EventCandidate]:
    """Annotate events with source breadth and demote unsupported high confidence.

    A single media write-up should not carry high confidence: the project rule is that
    high-priority claims need a primary source or two independent reliable sources.
    """
    for event in events:
        count = independent_sources(event)
        tier = best_tier(event, tiers_by_domain)

        if count >= 3 and not event.is_watchlist:
            if "多源确认" not in event.tags:
                event.tags.append("多源确认")
        elif count == 1:
            if "单一来源" not in event.tags:
                event.tags.append("单一来源")

        if tier == "T2" and count < 2 and event.confidence == Confidence.HIGH:
            event.confidence = Confidence.MEDIUM
            note = "仅一家媒体来源，未取得一手或第二独立来源，置信度由 high 下调为 medium。"
            event.verification_notes = f"{event.verification_notes} {note}".strip()

    return events


def promote_important_watchlist(
    events: list[EventCandidate],
    threshold: float = 0.85,
) -> list[EventCandidate]:
    """Keep important but contested stories in the report body.

    A big story whose *acceptance* is still pending (a contested proof, an unconfirmed
    filing) used to be tagged watchlist by the verification model and then vanish from
    "most important N", because that section excludes watchlist items. Importance and
    confidence are different axes: the story is still the story of the week, so it stays in
    the body with an explicit 待观察 tag, and the uncertainty lives in the notes.
    """
    promoted: list[EventCandidate] = []
    for event in events:
        if not event.is_watchlist:
            continue
        if event.confidence == Confidence.LOW or event.importance_score < threshold:
            continue
        event.is_watchlist = False
        if "待观察" not in event.tags:
            event.tags.append("待观察")
        note = "重要性达到正文门槛，虽有待核验成分仍保留在正文；结论接受度见分歧/反证与核验说明。"
        if note not in event.verification_notes:
            event.verification_notes = f"{event.verification_notes} {note}".strip()
        promoted.append(event)
    return promoted


def apply_confidence_policy(events: list[EventCandidate]) -> tuple[list[EventCandidate], int]:
    accepted: list[EventCandidate] = []
    filtered_ai_low = 0

    for event in events:
        if is_ai_topic(event.topic) and event.confidence == Confidence.LOW:
            filtered_ai_low += 1
            continue
        if not is_ai_topic(event.topic) and event.confidence == Confidence.LOW:
            event.is_watchlist = True
            if "待观察" not in event.tags:
                event.tags.append("待观察")
        accepted.append(event)

    return accepted, filtered_ai_low


def select_report_events(
    ranked: list[EventCandidate],
    max_body: int,
    max_watchlist: int,
) -> tuple[list[EventCandidate], int]:
    """Bound what the email actually contains.

    Everything still goes to the database; only the report body is capped so a busy day
    does not turn into a fifteen-minute read. Watchlist items get their own budget.
    """
    body: list[EventCandidate] = []
    watch: list[EventCandidate] = []
    for event in ranked:
        if event.is_watchlist:
            if len(watch) < max_watchlist:
                watch.append(event)
        elif len(body) < max_body:
            body.append(event)

    kept = body + watch
    overflow = max(0, len(ranked) - len(kept))
    return kept, overflow


def rank_events(
    events: list[EventCandidate],
    tiers_by_domain: dict[str, str] | None = None,
) -> list[EventCandidate]:
    confidence_weight = {
        Confidence.HIGH: 0.18,
        Confidence.MEDIUM: 0.08,
        Confidence.LOW: -0.08,
    }
    tier_bonus = {"T1": 0.06, "T1_5": 0.03, "T2": 0.0}

    def score(event: EventCandidate) -> float:
        # Breadth is counted by independent domain, so syndicated copies do not inflate it.
        source_bonus = min(max(independent_sources(event) - 1, 0) * 0.04, 0.16)
        tier = best_tier(event, tiers_by_domain)
        primary_bonus = tier_bonus.get(tier or "", 0.0)
        contradiction_penalty = min(len(event.contradictions) * 0.05, 0.2)
        watchlist_penalty = 0.08 if event.is_watchlist else 0.0
        return (
            event.importance_score
            + confidence_weight[event.confidence]
            + source_bonus
            + primary_bonus
            - contradiction_penalty
            - watchlist_penalty
        )

    return sorted(events, key=score, reverse=True)

"""
Computing reputation from recorded behaviour.

Every number here is derived from rows the system wrote while doing its job:
offers sent and answered, rescues bound and completed, escrows disputed,
ratings left by counterparties. Nothing is supplied by the company being
scored, and nothing is invented for companies with no history.

The old implementation had two failure modes worth remembering. `/trust-score`
accepted the statistics as a request body, so a company's reputation was
whatever the caller claimed. `/fulfilment-rating` returned a fixed 4.96 stars
and 1240 completed trips for *any* company id, including ones that did not
exist -- so an operator checking a stranger's track record was reading a
constant.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Company
from shared.observability import logger

from .offer_models import OfferState, RescueOffer
from .reputation_models import CompanyReputation, RescueRating

#: A company with fewer completed rescues than this is shown as unproven
#: rather than as good or bad. Two five-star ratings is not a track record,
#: and presenting it as one is how rating systems mislead.
MIN_RESCUES_FOR_CONFIDENCE = 5

#: The score a company starts from. Neutral: neither trusted nor suspect.
BASELINE_SCORE = 75.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


async def recompute_reputation(
    session: AsyncSession,
    company_id: str,
    commit: bool = True,
) -> CompanyReputation:
    """Recalculate one company's reputation from its recorded history."""
    offers_received = await session.scalar(
        select(func.count())
        .select_from(RescueOffer)
        .where(RescueOffer.carrier_company_id == company_id)
    ) or 0
    offers_accepted = await session.scalar(
        select(func.count())
        .select_from(RescueOffer)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.carrier_accepted_at.is_not(None))
    ) or 0
    offers_declined = await session.scalar(
        select(func.count())
        .select_from(RescueOffer)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.state == OfferState.DECLINED.value)
    ) or 0
    offers_expired = await session.scalar(
        select(func.count())
        .select_from(RescueOffer)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.state == OfferState.EXPIRED.value)
    ) or 0
    rescues_completed = await session.scalar(
        select(func.count())
        .select_from(RescueOffer)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.state == OfferState.BOUND.value)
        .where(RescueOffer.bound_at.is_not(None))
    ) or 0
    cancelled_after_bind = await session.scalar(
        select(func.count())
        .select_from(RescueOffer)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.state == OfferState.CANCELLED.value)
    ) or 0

    rating_count = await session.scalar(
        select(func.count())
        .select_from(RescueRating)
        .where(RescueRating.subject_company_id == company_id)
    ) or 0
    rating_sum = await session.scalar(
        select(func.coalesce(func.sum(RescueRating.stars), 0))
        .where(RescueRating.subject_company_id == company_id)
    ) or 0

    avg_response = await _average_response_seconds(session, company_id)

    reputation = await session.get(CompanyReputation, company_id)
    if reputation is None:
        reputation = CompanyReputation(company_id=company_id)
        session.add(reputation)

    reputation.offers_received = offers_received
    reputation.offers_accepted = offers_accepted
    reputation.offers_declined = offers_declined
    reputation.offers_expired = offers_expired
    reputation.rescues_completed = rescues_completed
    reputation.rescues_cancelled_after_bind = cancelled_after_bind
    reputation.rating_count = rating_count
    reputation.rating_sum = int(rating_sum)
    reputation.avg_response_seconds = avg_response

    score, signals = score_reputation(reputation)
    reputation.trust_score = score
    reputation.signals_json = signals
    reputation.computed_at = datetime.now(timezone.utc)

    # Keep the denormalised copy on companies in step, since matching and the
    # offer card both read it on a hot path.
    company = await session.get(Company, company_id)
    if company is not None:
        company.trust_score = score

    if commit:
        await session.commit()
        await session.refresh(reputation)
    else:
        await session.flush()

    logger.info("reputation_recomputed", company_id=company_id, trust_score=score)
    return reputation


async def _average_response_seconds(
    session: AsyncSession, company_id: str
) -> Optional[float]:
    """How quickly this carrier answers an offer, when they answer at all."""
    result = await session.execute(
        select(RescueOffer.created_at, RescueOffer.carrier_accepted_at)
        .where(RescueOffer.carrier_company_id == company_id)
        .where(RescueOffer.carrier_accepted_at.is_not(None))
    )
    deltas: List[float] = []
    for created, accepted in result.all():
        if created and accepted:
            created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            accepted = accepted if accepted.tzinfo else accepted.replace(tzinfo=timezone.utc)
            deltas.append((accepted - created).total_seconds())
    if not deltas:
        return None
    return sum(deltas) / len(deltas)


def score_reputation(rep: CompanyReputation) -> tuple[float, List[Dict[str, Any]]]:
    """Turn the counters into a score plus the reasons behind it.

    The signals matter as much as the number. An owner deciding whether to
    hand a stranger 5 lakh of insulin needs to know *why* a carrier scores
    what they do, not just that a box says 92.
    """
    score = BASELINE_SCORE
    signals: List[Dict[str, Any]] = []

    if rep.rescues_completed == 0 and rep.rating_count == 0:
        return (
            BASELINE_SCORE,
            [
                {
                    "kind": "neutral",
                    "label": "New to the network",
                    "detail": "No completed rescues yet. Score is the starting baseline.",
                }
            ],
        )

    # --- completed work ---------------------------------------------------
    if rep.rescues_completed:
        bonus = min(12.0, rep.rescues_completed * 0.6)
        score += bonus
        confident = rep.rescues_completed >= MIN_RESCUES_FOR_CONFIDENCE
        signals.append(
            {
                "kind": "positive" if confident else "neutral",
                "label": f"{rep.rescues_completed} completed rescue(s)",
                "detail": (
                    "Established track record."
                    if confident
                    else "Limited history so far -- treat the score as provisional."
                ),
            }
        )

    # --- peer ratings -----------------------------------------------------
    avg = rep.average_stars
    if avg is not None:
        score += (avg - 3.0) * 6.0
        signals.append(
            {
                "kind": "positive" if avg >= 4.0 else "negative" if avg < 3.0 else "neutral",
                "label": f"{avg:.1f} stars from {rep.rating_count} rating(s)",
                "detail": "Rated by both cargo owners and carriers they have worked with.",
            }
        )

    # --- responsiveness ---------------------------------------------------
    acceptance = rep.acceptance_rate
    if acceptance is not None and rep.offers_received >= 3:
        if acceptance >= 0.6:
            score += 5.0
            signals.append(
                {
                    "kind": "positive",
                    "label": f"Accepts {acceptance:.0%} of offers",
                    "detail": "Reliably available when called.",
                }
            )
        elif acceptance < 0.25:
            score -= 8.0
            signals.append(
                {
                    "kind": "negative",
                    "label": f"Accepts only {acceptance:.0%} of offers",
                    "detail": "Often unavailable, which delays matching.",
                }
            )

    if rep.offers_expired >= 3:
        score -= min(10.0, rep.offers_expired * 1.5)
        signals.append(
            {
                "kind": "negative",
                "label": f"{rep.offers_expired} offer(s) left to expire",
                "detail": "Not answering costs the stranded carrier time they do not have.",
            }
        )

    if rep.avg_response_seconds is not None and rep.offers_accepted >= 3:
        mins = rep.avg_response_seconds / 60.0
        if mins <= 2.0:
            score += 4.0
            signals.append(
                {
                    "kind": "positive",
                    "label": f"Responds in {mins:.1f} min on average",
                    "detail": "Fast to commit.",
                }
            )

    # --- failures ---------------------------------------------------------
    if rep.rescues_cancelled_after_bind:
        penalty = rep.rescues_cancelled_after_bind * 9.0
        score -= penalty
        signals.append(
            {
                "kind": "negative",
                "label": f"{rep.rescues_cancelled_after_bind} rescue(s) abandoned after committing",
                "detail": "Weighted heavily: the owner had stopped looking for anyone else.",
            }
        )

    if rep.disputes_raised_against:
        score -= rep.disputes_raised_against * 7.0
        signals.append(
            {
                "kind": "negative",
                "label": f"{rep.disputes_raised_against} dispute(s) raised against them",
                "detail": "Cargo arrived outside its agreed condition.",
            }
        )
    elif rep.rescues_completed >= MIN_RESCUES_FOR_CONFIDENCE:
        signals.append(
            {
                "kind": "positive",
                "label": "No disputes on record",
                "detail": f"Across {rep.rescues_completed} completed rescues.",
            }
        )

    if rep.condition_breaches:
        score -= rep.condition_breaches * 5.0
        signals.append(
            {
                "kind": "negative",
                "label": f"{rep.condition_breaches} cold-chain breach(es)",
                "detail": "Recorded by telemetry during carriage.",
            }
        )

    return round(_clamp(score), 1), signals


async def get_reputation(
    session: AsyncSession, company_id: str, recompute_if_missing: bool = True
) -> Optional[CompanyReputation]:
    reputation = await session.get(CompanyReputation, company_id)
    if reputation is None and recompute_if_missing:
        company = await session.get(Company, company_id)
        if company is None:
            # No invented fallback identity. A company that does not exist has
            # no reputation, and saying so is the honest answer.
            return None
        reputation = await recompute_reputation(session, company_id)
    return reputation


async def trust_scores_for(
    session: AsyncSession, company_ids: List[str]
) -> Dict[str, float]:
    """Bulk lookup for the matching hot path."""
    if not company_ids:
        return {}
    result = await session.execute(
        select(CompanyReputation.company_id, CompanyReputation.trust_score).where(
            CompanyReputation.company_id.in_(company_ids)
        )
    )
    scores = {cid: score for cid, score in result.all()}
    # Companies with no reputation row yet sit at the neutral baseline.
    return {cid: scores.get(cid, BASELINE_SCORE) for cid in company_ids}


async def acceptance_rates_for(
    session: AsyncSession, company_ids: List[str]
) -> Dict[str, float]:
    """Historical offer-acceptance rate, for the rescue score."""
    if not company_ids:
        return {}
    result = await session.execute(
        select(
            CompanyReputation.company_id,
            CompanyReputation.offers_received,
            CompanyReputation.offers_accepted,
        ).where(CompanyReputation.company_id.in_(company_ids))
    )
    rates: Dict[str, float] = {}
    for cid, received, accepted in result.all():
        # An unproven carrier is assumed neutral rather than perfect. Assuming
        # 0.95 for everyone, as the old code did, meant this factor could
        # never distinguish anybody.
        rates[cid] = (accepted / received) if received else 0.5
    return {cid: rates.get(cid, 0.5) for cid in company_ids}

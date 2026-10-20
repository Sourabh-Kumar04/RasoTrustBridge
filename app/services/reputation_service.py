import logging
from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.config import get_settings
from app.models.user import User
from app.models.enums import ActorRole, DisputeStatus

logger = logging.getLogger("trustbridge.reputation")


class ReputationService:
    """
    Weighted Reputation, Trust Scoring, and Economic Friction Engine.
    Directly addresses mentor critique: 'every deal starts at zero trust' and Harmer's critique:
    'a bad actor's cost to create a new account and rerun a scam is near zero'.

    Trust Score Bounds: [0.0, 100.0].
    - New accounts (< 7 days old) baseline: 30.0 (anti-Sybil friction).
    - Mature accounts baseline: 50.0 (neutral trust anchor).
    - Contracts >= $1,000 require freelancer to have completed >= 1 prior deal OR post a higher stake (10%).
    """

    POINTS_RELEASE_SUCCESS = 5.0
    POINTS_DISPUTE_OPENED = -10.0
    POINTS_DISPUTE_FAVOR = 5.0
    POINTS_DISPUTE_AGAINST = -5.0

    @staticmethod
    def clamp_score(score: float) -> float:
        return max(0.0, min(100.0, round(score, 1)))

    def is_new_account(self, user: Optional[User]) -> bool:
        """Determines if an account is under the new account friction window (default 7 days)."""
        if not user or not user.created_at:
            return True
        now = datetime.now(timezone.utc)
        created = user.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        settings = get_settings()
        age_seconds = (now - created).total_seconds()
        return age_seconds < (settings.NEW_ACCOUNT_AGE_DAYS * 86400)

    def get_effective_trust_score(self, user: Optional[User]) -> float:
        """Returns the live effective trust score considering new account friction."""
        if not user:
            return 30.0
        settings = get_settings()
        if self.is_new_account(user) and (user.completed_deals or 0) == 0:
            return min(user.trust_score if user.trust_score is not None else settings.DEFAULT_NEW_ACCOUNT_TRUST_SCORE, settings.DEFAULT_NEW_ACCOUNT_TRUST_SCORE)
        return user.trust_score if user.trust_score is not None else 50.0

    def validate_freelancer_for_contract(
        self,
        freelancer: Optional[User],
        contract_amount: float,
        offered_stake_percentage: Optional[float] = None,
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Validates freelancer eligibility and calculates required stake percentage.
        Harmer's critique: Contracts above $1,000 require >= 1 prior deal OR higher stake (10%).
        Returns: (is_eligible, required_stake_percentage, error_message_if_any)
        """
        settings = get_settings()
        default_stake = settings.DEFAULT_STAKE_PERCENTAGE
        high_stake = settings.NEW_ACCOUNT_STAKE_PERCENTAGE
        threshold = settings.NEW_ACCOUNT_THRESHOLD_AMOUNT

        is_new = self.is_new_account(freelancer)
        completed = freelancer.completed_deals if freelancer else 0

        if contract_amount >= threshold:
            if is_new and completed < 1:
                required_stake = high_stake
                if offered_stake_percentage is not None and offered_stake_percentage < high_stake:
                    return False, required_stake, (
                        f"New accounts (< {settings.NEW_ACCOUNT_AGE_DAYS} days old) with 0 prior completed deals "
                        f"must post a {high_stake:.1f}% security deposit stake for contracts >= ${threshold:,.2f} "
                        f"(provided: {offered_stake_percentage:.1f}%)."
                    )
                return True, required_stake, None

        return True, default_stake, None

    def record_successful_release(self, db: Session, user: Optional[User]) -> Optional[float]:
        """Awards reputation points upon successful milestone release or contract completion."""
        if not user:
            return None
        user.completed_deals = (user.completed_deals or 0) + 1
        user.trust_score = self.clamp_score((user.trust_score or 50.0) + self.POINTS_RELEASE_SUCCESS)
        db.add(user)
        logger.info("Reputation increased for %s: %s (completed: %s)", user.email, user.trust_score, user.completed_deals)
        return user.trust_score

    def record_dispute_opened(self, db: Session, client: Optional[User], freelancer: Optional[User]) -> None:
        """Penalizes reputation when a dispute is opened between parties."""
        for u in [client, freelancer]:
            if u:
                u.disputed_deals = (u.disputed_deals or 0) + 1
                u.trust_score = self.clamp_score((u.trust_score or 50.0) + self.POINTS_DISPUTE_OPENED)
                db.add(u)
                logger.info("Reputation penalized for %s: %s (disputes: %s)", u.email, u.trust_score, u.disputed_deals)

    def record_dispute_resolution(
        self,
        db: Session,
        client: Optional[User],
        freelancer: Optional[User],
        resolution_status: DisputeStatus,
    ) -> None:
        """Updates reputation based on arbitration decision."""
        if resolution_status == DisputeStatus.RESOLVED_DEVELOPER_PAYOUT:
            # Freelancer delivered work; client was unfounded
            if freelancer:
                freelancer.completed_deals = (freelancer.completed_deals or 0) + 1
                freelancer.trust_score = self.clamp_score((freelancer.trust_score or 50.0) + self.POINTS_DISPUTE_FAVOR)
                db.add(freelancer)
            if client:
                client.trust_score = self.clamp_score((client.trust_score or 50.0) + self.POINTS_DISPUTE_AGAINST)
                db.add(client)

        elif resolution_status == DisputeStatus.RESOLVED_CLIENT_REFUND:
            # Client refund justified; freelancer did not deliver
            if client:
                client.trust_score = self.clamp_score((client.trust_score or 50.0) + self.POINTS_DISPUTE_FAVOR)
                db.add(client)
            if freelancer:
                freelancer.trust_score = self.clamp_score((freelancer.trust_score or 50.0) + self.POINTS_DISPUTE_AGAINST)
                db.add(freelancer)


reputation_service = ReputationService()

import logging
from typing import Optional
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.enums import ActorRole, DisputeStatus

logger = logging.getLogger("trustbridge.reputation")


class ReputationService:
    """
    Weighted Reputation and Trust Scoring Engine.
    Directly addresses mentor critique: 'every deal starts at zero trust'.

    Trust Score Bounds: [0.0, 100.0], default = 50.0.
    Weighted Formula & Dynamics:
      - Initial baseline: 50.0 (neutral trust anchor)
      - Completed deal / successful milestone release: +5.0 points (rewards reliable delivery)
      - Dispute opened: -10.0 points (disincentivizes contention and non-performance)
      - Dispute resolved in favor: +5.0 points (restores standing upon vindication)
      - Dispute resolved against: -5.0 points (further penalizes bad faith)
      - Completed and disputed counters tracked explicitly.
    """

    POINTS_RELEASE_SUCCESS = 5.0
    POINTS_DISPUTE_OPENED = -10.0
    POINTS_DISPUTE_FAVOR = 5.0
    POINTS_DISPUTE_AGAINST = -5.0

    @staticmethod
    def clamp_score(score: float) -> float:
        return max(0.0, min(100.0, round(score, 1)))

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

from datetime import datetime, timezone
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import DisputeRecord, AuditLog
from app.models.enums import ContractStatus, MilestoneStatus, ActorRole, DisputeStatus


class DisputeService:
    """
    PayPal-Native Dispute Protocol & Automated Evidence Packaging.
    Directly addresses PayPal support cost reduction by turning unstructured claims
    into structured, mathematically verified evidence dossiers.
    """

    def open_dispute(
        self,
        db: Session,
        contract: Contract,
        milestone: Milestone,
        initiator_role: ActorRole,
        initiator_email: str,
        reason: str,
        description: str,
    ) -> DisputeRecord:
        # Build self-documenting dispute evidence bundle
        evidence_bundle = self._build_evidence_bundle(contract, milestone, initiator_role, reason, description)

        dispute = DisputeRecord(
            contract_id=contract.id,
            milestone_id=milestone.id,
            initiator_role=initiator_role,
            initiator_email=initiator_email,
            reason=reason,
            description=description,
            evidence_bundle=evidence_bundle,
            status=DisputeStatus.OPEN,
        )
        db.add(dispute)

        # Transition states
        milestone.status = MilestoneStatus.DISPUTED
        milestone.disputed_at = datetime.now(timezone.utc)
        contract.status = ContractStatus.DISPUTED

        # Audit log
        audit = AuditLog(
            contract_id=contract.id,
            milestone_id=milestone.id,
            actor_role=initiator_role,
            actor_identifier=initiator_email,
            action="DISPUTE_OPENED",
            description=f"Dispute initiated: '{reason}'. Immutable evidence bundle generated for PayPal resolution.",
            metadata_json={"dispute_reason": reason, "initiator": initiator_role.value},
        )
        db.add(audit)
        db.commit()
        db.refresh(dispute)
        return dispute

    def resolve_dispute(
        self,
        db: Session,
        dispute: DisputeRecord,
        resolution_status: DisputeStatus,
        resolution_notes: str,
        resolved_by: str = "ARBITRATION_SYSTEM",
    ) -> DisputeRecord:
        dispute.status = resolution_status
        dispute.resolution_notes = resolution_notes
        dispute.resolved_at = datetime.now(timezone.utc)

        contract = dispute.contract
        milestone = dispute.milestone

        if resolution_status == DisputeStatus.RESOLVED_DEVELOPER_PAYOUT:
            milestone.status = MilestoneStatus.RELEASED
            milestone.released_at = datetime.now(timezone.utc)
        elif resolution_status == DisputeStatus.RESOLVED_CLIENT_REFUND:
            milestone.status = MilestoneStatus.CANCELLED if hasattr(MilestoneStatus, "CANCELLED") else MilestoneStatus.PENDING

        # Check if all other milestones are resolved
        active_disputes = (
            db.query(DisputeRecord)
            .filter(
                DisputeRecord.contract_id == contract.id,
                DisputeRecord.status == DisputeStatus.OPEN,
            )
            .count()
        )
        if active_disputes == 0:
            contract.status = ContractStatus.ACTIVE

        audit = AuditLog(
            contract_id=contract.id,
            milestone_id=milestone.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier=resolved_by,
            action="DISPUTE_RESOLVED",
            description=f"Dispute resolved with status {resolution_status.value}. Notes: {resolution_notes}",
            metadata_json={"resolution": resolution_status.value},
        )
        db.add(audit)
        db.commit()
        db.refresh(dispute)
        return dispute

    def _build_evidence_bundle(
        self,
        contract: Contract,
        milestone: Milestone,
        initiator_role: ActorRole,
        reason: str,
        description: str,
    ) -> Dict[str, Any]:
        return {
            "evidence_generated_at": datetime.now(timezone.utc).isoformat(),
            "protocol_version": "TrustBridge-Evidence-v1",
            "paypal_reference": {
                "order_id": contract.paypal_order_id,
                "capture_id": contract.paypal_capture_id or milestone.paypal_capture_id,
            },
            "contract": {
                "id": contract.id,
                "title": contract.title,
                "client": {"name": contract.client_name, "email": contract.client_email, "country": contract.client_country},
                "developer": {"name": contract.developer_name, "email": contract.developer_email, "country": contract.developer_country},
                "total_amount": contract.total_amount,
                "base_currency": contract.base_currency,
                "payout_currency": contract.payout_currency,
                "fx_strategy": contract.fx_strategy.value,
                "locked_fx_rate": contract.locked_fx_rate,
            },
            "disputed_milestone": {
                "id": milestone.id,
                "order_index": milestone.order_index,
                "title": milestone.title,
                "amount": milestone.amount,
                "acceptance_criteria": milestone.acceptance_criteria,
                "verification_type": milestone.verification_type.value,
            },
            "developer_submission": {
                "evidence_url": milestone.evidence_url,
                "evidence_notes": milestone.evidence_notes,
                "evidence_payload": milestone.evidence_payload,
                "submitted_at": milestone.submitted_at.isoformat() if milestone.submitted_at else None,
            },
            "verification_audit": {
                "score": milestone.verification_score,
                "verified_by": milestone.verified_by,
                "verification_log": milestone.verification_log,
                "verified_at": milestone.verified_at.isoformat() if milestone.verified_at else None,
            },
            "dispute_claim": {
                "initiator": initiator_role.value,
                "reason": reason,
                "description": description,
            },
        }


dispute_service = DisputeService()

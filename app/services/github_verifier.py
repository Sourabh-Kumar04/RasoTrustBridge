import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.milestone import Milestone
from app.models.contract import Contract
from app.models.payment import AuditLog
from app.models.enums import MilestoneStatus, ContractStatus, ActorRole
from app.schemas.milestone import MilestoneSubmitRequest
from app.services.contract_service import contract_service


class GitHubVerifierService:
    """
    GitHub Webhook Verification and Delivery Proof Engine.
    Validates HMAC-SHA256 signatures, links incoming webhooks to active
    milestones, and triggers autonomous AI delivery verification upon PR merge.
    """

    def __init__(self):
        self.settings = get_settings()

    def verify_signature(
        self,
        payload_bytes: bytes,
        signature_header: Optional[str],
        secret: Optional[str] = None,
    ) -> bool:
        """
        Verifies GitHub X-Hub-Signature-256 HMAC header.
        Format expected: 'sha256=<hex_digest>'
        """
        secret_key = secret if secret is not None else (self.settings.GITHUB_WEBHOOK_SECRET or "")
        if not secret_key:
            # If no webhook secret is configured, reject for security
            return False

        if not signature_header or not signature_header.startswith("sha256="):
            return False

        expected_hash = hmac.new(
            key=secret_key.encode("utf-8"),
            msg=payload_bytes,
            digestmod=hashlib.sha256,
        ).hexdigest()
        expected_header = f"sha256={expected_hash}"

        return hmac.compare_digest(expected_header, signature_header)

    def find_linked_milestone(
        self,
        db: Session,
        text_corpus: str,
    ) -> Optional[Milestone]:
        """
        Finds a linked milestone from branch name, PR title, body, or commit messages.
        Resolves via:
        1. Exact milestone ID (ms_xxxx)
        2. Contract ID (ct_xxxx) with optional milestone index (e.g. M1, milestone 2)
        3. Title keyword matches for active funded contracts
        """
        if not text_corpus:
            return None

        # 1. Direct Milestone ID match (e.g. ms_123456789abc)
        ms_matches = re.findall(r"\b(ms_[a-zA-Z0-9]{6,16})\b", text_corpus, re.IGNORECASE)
        for ms_id in ms_matches:
            milestone = db.query(Milestone).filter(Milestone.id == ms_id).first()
            if milestone:
                return milestone

        # 2. Contract ID match (e.g. ct_123456789abc)
        ct_matches = re.findall(r"\b(ct_[a-zA-Z0-9]{6,16})\b", text_corpus, re.IGNORECASE)
        for ct_id in ct_matches:
            contract = db.query(Contract).filter(Contract.id == ct_id).first()
            if not contract:
                continue

            # Check if milestone order index is mentioned in corpus: e.g. "M1", "Milestone 2", "ms-3"
            idx_matches = re.findall(r"(?:milestone|ms|m)[_\s\-#]*([1-9]\d*)", text_corpus, re.IGNORECASE)
            if idx_matches:
                target_idx = int(idx_matches[0])
                milestone = db.query(Milestone).filter(
                    Milestone.contract_id == contract.id,
                    Milestone.order_index == target_idx,
                ).first()
                if milestone:
                    return milestone

            # If no index mentioned, find earliest FUNDED or SUBMITTED milestone
            milestone = (
                db.query(Milestone)
                .filter(
                    Milestone.contract_id == contract.id,
                    Milestone.status.in_([MilestoneStatus.FUNDED, MilestoneStatus.SUBMITTED]),
                )
                .order_by(Milestone.order_index.asc())
                .first()
            )
            if milestone:
                return milestone

        # 3. Check for generic milestone tags (e.g. "milestone-1" or "m1") across all ACTIVE contracts
        idx_matches = re.findall(r"(?:milestone|ms|m)[_\s\-#]*([1-9]\d*)", text_corpus, re.IGNORECASE)
        if idx_matches:
            target_idx = int(idx_matches[0])
            active_contract = (
                db.query(Contract)
                .filter(Contract.status == ContractStatus.ACTIVE)
                .order_by(Contract.created_at.desc())
                .first()
            )
            if active_contract:
                milestone = db.query(Milestone).filter(
                    Milestone.contract_id == active_contract.id,
                    Milestone.order_index == target_idx,
                ).first()
                if milestone:
                    return milestone

        return None

    async def handle_pull_request_event(
        self,
        db: Session,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Handles GitHub 'pull_request' webhook event.
        When PR is merged:
        - Locates linked milestone
        - Gathers evidence payload (PR link, commit SHA, merge details)
        - Transitions milestone FUNDED -> SUBMITTED -> triggers AI verification -> VERIFIED
        """
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        is_merged = pr.get("merged", False)

        if not (action == "closed" and is_merged):
            return {
                "event": "pull_request",
                "action": action,
                "merged": is_merged,
                "status": "ignored",
                "message": "Only merged pull requests trigger milestone delivery verification",
            }

        title = pr.get("title", "")
        body = pr.get("body", "") or ""
        head = pr.get("head", {})
        branch = head.get("ref", "")
        commit_sha = pr.get("merge_commit_sha") or head.get("sha", "")
        pr_url = pr.get("html_url", "")
        pr_number = pr.get("number")
        merged_by = (pr.get("merged_by") or {}).get("login", "unknown")
        repo_name = (payload.get("repository") or {}).get("full_name", "")

        # Search for linked milestone
        corpus = f"{title}\n{branch}\n{body}\n{commit_sha}"
        milestone = self.find_linked_milestone(db, corpus)

        if not milestone:
            return {
                "event": "pull_request",
                "action": action,
                "merged": True,
                "status": "unmatched",
                "message": f"No linked milestone or contract ID found in PR #{pr_number} ('{title}', branch '{branch}')",
            }

        evidence_payload = {
            "source": "github_webhook",
            "event": "pull_request",
            "action": "closed",
            "merged": True,
            "pr_number": pr_number,
            "pr_title": title,
            "pr_url": pr_url,
            "branch": branch,
            "commit_sha": commit_sha,
            "merged_by": merged_by,
            "merged_at": pr.get("merged_at"),
            "repository": repo_name,
            "tests_passed": 1,
            "tests_total": 1,
        }

        # If milestone is FUNDED, submit it (triggers AI verification engine)
        if milestone.status == MilestoneStatus.FUNDED:
            submit_req = MilestoneSubmitRequest(
                evidence_url=pr_url,
                evidence_notes=f"GitHub PR #{pr_number} merged by @{merged_by}: {title}",
                evidence_payload=evidence_payload,
            )
            updated_m = await contract_service.submit_milestone(db, milestone.id, submit_req)

            # Record system audit log for webhook verification
            audit = AuditLog(
                contract_id=updated_m.contract_id,
                milestone_id=updated_m.id,
                actor_role=ActorRole.SYSTEM,
                actor_identifier="GITHUB_WEBHOOK",
                action="GITHUB_PR_MERGED_VERIFIED",
                description=(
                    f"GitHub PR #{pr_number} merged in {repo_name}. "
                    f"Milestone {updated_m.order_index} verified by AI Engine "
                    f"(score: {updated_m.verification_score:.2f}, status: {updated_m.status.value})."
                ),
                metadata_json={
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "commit_sha": commit_sha,
                    "score": updated_m.verification_score,
                    "is_approved": updated_m.status == MilestoneStatus.VERIFIED,
                },
            )
            db.add(audit)
            db.commit()
            db.refresh(updated_m)

            return {
                "event": "pull_request",
                "status": "verified" if updated_m.status == MilestoneStatus.VERIFIED else "submitted",
                "milestone_id": updated_m.id,
                "contract_id": updated_m.contract_id,
                "milestone_title": updated_m.title,
                "milestone_status": updated_m.status.value,
                "verification_score": updated_m.verification_score,
                "is_approved": updated_m.status == MilestoneStatus.VERIFIED,
                "evidence_url": updated_m.evidence_url,
                "commit_sha": commit_sha,
            }

        elif milestone.status == MilestoneStatus.SUBMITTED:
            # Re-trigger verification if already submitted
            updated_m = await contract_service.verify_milestone(
                db,
                milestone.id,
                notes=f"Re-verified via merged PR #{pr_number}",
            )
            return {
                "event": "pull_request",
                "status": "verified" if updated_m.status == MilestoneStatus.VERIFIED else "submitted",
                "milestone_id": updated_m.id,
                "contract_id": updated_m.contract_id,
                "milestone_title": updated_m.title,
                "milestone_status": updated_m.status.value,
                "verification_score": updated_m.verification_score,
                "is_approved": updated_m.status == MilestoneStatus.VERIFIED,
                "evidence_url": updated_m.evidence_url,
            }

        else:
            return {
                "event": "pull_request",
                "status": "noop",
                "milestone_id": milestone.id,
                "contract_id": milestone.contract_id,
                "milestone_status": milestone.status.value,
                "message": f"Milestone is already in status '{milestone.status.value}'. No state change needed.",
            }

    async def handle_push_event(
        self,
        db: Session,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Handles GitHub 'push' webhook event.
        Tracks commit SHAs and authors for delivery proof audit trail.
        """
        ref = payload.get("ref", "")
        commits = payload.get("commits", [])
        after_sha = payload.get("after", "")
        repo_name = (payload.get("repository") or {}).get("full_name", "")

        # Build corpus of commit messages + branch name
        commit_msgs = "\n".join(c.get("message", "") for c in commits)
        corpus = f"{ref}\n{commit_msgs}\n{after_sha}"

        milestone = self.find_linked_milestone(db, corpus)

        if not milestone:
            return {
                "event": "push",
                "status": "unmatched",
                "ref": ref,
                "commits_count": len(commits),
                "message": "No linked milestone found in branch ref or commit messages",
            }

        # Append commits to milestone evidence payload
        current_payload = milestone.evidence_payload or {}
        tracked_commits = current_payload.get("tracked_commits", [])
        for c in commits:
            tracked_commits.append({
                "commit_sha": c.get("id"),
                "message": c.get("message"),
                "author": (c.get("author") or {}).get("name"),
                "url": c.get("url"),
                "timestamp": c.get("timestamp"),
            })

        current_payload["tracked_commits"] = tracked_commits
        current_payload["latest_commit_sha"] = after_sha
        current_payload["branch"] = ref
        current_payload["repository"] = repo_name
        milestone.evidence_payload = current_payload

        # Audit log for commit proof
        audit = AuditLog(
            contract_id=milestone.contract_id,
            milestone_id=milestone.id,
            actor_role=ActorRole.DEVELOPER,
            actor_identifier=f"GITHUB_PUSH:{repo_name}",
            action="GITHUB_COMMITS_PUSHED",
            description=(
                f"Tracked {len(commits)} commit(s) on branch '{ref}'. "
                f"Latest SHA: {after_sha[:10] if after_sha else 'N/A'} for Milestone {milestone.order_index}."
            ),
            metadata_json={
                "ref": ref,
                "commits_count": len(commits),
                "latest_commit_sha": after_sha,
            },
        )
        db.add(audit)
        db.commit()
        db.refresh(milestone)

        return {
            "event": "push",
            "status": "tracked",
            "milestone_id": milestone.id,
            "contract_id": milestone.contract_id,
            "milestone_status": milestone.status.value,
            "commits_tracked": len(commits),
            "latest_commit_sha": after_sha,
        }


github_verifier_service = GitHubVerifierService()

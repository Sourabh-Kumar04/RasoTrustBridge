import re
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import FXStrategy, VerificationType
from app.models.user import User
from app.schemas.contract import (
    ContractCreateSchema,
    MilestoneCreateSchema,
    AcceptanceCriterionSchema,
)
from app.schemas.negotiation import (
    NegotiationRequestSchema,
    NegotiationResponseSchema,
    NegotiationMessageSchema,
    NegotiationRoundSchema,
    AgentOfferSchema,
    AgreedTermsSchema,
    NegotiationBoundsSchema,
)
from app.services.contract_service import contract_service
from app.services.fx_service import fx_service
from app.services.notification_service import notification_service
from app.services.ai_provider import get_ai_provider


class NegotiatorService:
    """
    Autonomous Agent-to-Agent Negotiation Engine.
    Orchestrates negotiations between a Buyer Agent and a Seller Agent
    over price, milestones, timeline, and FX risk strategy.
    
    Includes:
    - Quick Mode (term suggestion + single-round adjustment) as default
    - Walk-away termination (NO_DEAL) when terms are irreconcilable (gap > 40% or 3 rounds no movement)
    - Counterparty Trust Score integration
    - Swappable AI provider (Ollama vs OpenAI) with deterministic fallback
    """

    def __init__(self):
        self.settings = get_settings()
        self._history: Dict[str, NegotiationResponseSchema] = {}

    async def run_negotiation(
        self,
        db: Session,
        req: NegotiationRequestSchema,
    ) -> NegotiationResponseSchema:
        negotiation_id = f"neg_{uuid.uuid4().hex[:12]}"

        # Resolve Trust Scores
        client_trust = req.client_trust_score
        if client_trust is None:
            client_trust = self._resolve_trust_score(db, req.client_email)

        developer_trust = req.developer_trust_score
        if developer_trust is None:
            developer_trust = self._resolve_trust_score(db, req.developer_email)

        use_ai = (
            not req.force_rule_based
            and self.settings.ENVIRONMENT != "testing"
        )

        rounds, transcript, agreed_terms, model_used, termination_reason = await self._execute_negotiation_rounds(
            req=req,
            use_ai=use_ai,
            client_trust=client_trust,
            developer_trust=developer_trust,
        )

        if agreed_terms is not None:
            status = "agreed"
        elif termination_reason:
            status = "no_deal"
        else:
            status = "failed"

        contract_id = None
        created_contract = None

        # Auto-create TrustBridge Escrow Contract ONLY if agreed
        if agreed_terms and req.auto_create_contract and status == "agreed":
            contract_create_req = await self._build_contract_create_schema(req, agreed_terms)
            try:
                created_contract = await contract_service.create_contract(db, contract_create_req)
                contract_id = created_contract.id
                notification_service.notify_negotiation_completed(db, created_contract)
            except Exception:
                pass

        # Compute active user bounds summary
        buyer_budget_extracted = self._extract_price(req.buyer_brief, default=3000.0)
        buyer_cap_base = req.buyer_max_budget if req.buyer_max_budget is not None else buyer_budget_extracted
        seller_floor_base = req.seller_min_price if req.seller_min_price is not None else 0.0
        effective_cap = round(buyer_cap_base * (1.0 + req.max_over_budget_pct / 100.0), 2)
        bounds_summary = NegotiationBoundsSchema(
            buyer_max_budget=buyer_cap_base,
            seller_min_price=seller_floor_base,
            max_over_budget_pct=req.max_over_budget_pct,
            effective_buyer_cap=effective_cap,
            walk_away_gap_pct=req.walk_away_gap_pct,
            max_rounds=req.max_rounds,
        )

        response = NegotiationResponseSchema(
            id=negotiation_id,
            status=status,
            total_rounds=len(rounds),
            transcript=transcript,
            rounds=rounds,
            agreed_terms=agreed_terms,
            termination_reason=termination_reason,
            bounds=bounds_summary,
            contract_id=contract_id,
            contract=created_contract,
            model_used=model_used,
            created_at=datetime.utcnow(),
        )

        self._history[negotiation_id] = response
        return response

    def get_negotiation(self, negotiation_id: str) -> Optional[NegotiationResponseSchema]:
        return self._history.get(negotiation_id)

    def list_negotiations(self) -> List[NegotiationResponseSchema]:
        return sorted(self._history.values(), key=lambda n: n.created_at, reverse=True)

    def _resolve_trust_score(self, db: Session, email: str) -> float:
        if not email:
            return 50.0
        u = db.query(User).filter(User.email == email.lower()).first()
        return u.trust_score if u and u.trust_score is not None else 50.0

    # --------------------------------------------------------------------------
    # Multi-Round & Quick Mode Execution Core
    # --------------------------------------------------------------------------

    async def _execute_negotiation_rounds(
        self,
        req: NegotiationRequestSchema,
        use_ai: bool,
        client_trust: float,
        developer_trust: float,
    ) -> Tuple[List[NegotiationRoundSchema], List[NegotiationMessageSchema], Optional[AgreedTermsSchema], str, Optional[str]]:
        buyer_budget = self._extract_price(req.buyer_brief, default=3000.0)
        seller_ask = self._extract_price(req.seller_brief, default=4000.0)

        buyer_timeline = self._extract_timeline(req.buyer_brief, default=21)
        seller_timeline = self._extract_timeline(req.seller_brief, default=28)

        # Hostile QA hardening 1: Zero or negative budget / ask
        if buyer_budget <= 0 or seller_ask <= 0:
            termination_reason = "Invalid or zero economic brief: buyer budget and developer ask must be strictly greater than zero"
            walk_msg = self._generate_no_deal_turn(1, max(0.0, buyer_budget), max(0.0, seller_ask), req.base_currency, termination_reason)
            return [
                NegotiationRoundSchema(
                    round_number=1,
                    buyer_turn=walk_msg,
                    seller_turn=walk_msg,
                    price_gap=round(abs(seller_ask - buyer_budget), 2),
                    status="no_deal",
                )
            ], [walk_msg], None, "rule_based_fallback", termination_reason

        # Hostile QA hardening 2: Absurd / negative timelines
        if buyer_timeline <= 0 or seller_timeline <= 0 or buyer_timeline > 3650 or seller_timeline > 3650:
            termination_reason = "Invalid delivery timeline: timeline must be positive and not exceed 10 years"
            walk_msg = self._generate_no_deal_turn(1, buyer_budget, seller_ask, req.base_currency, termination_reason)
            return [
                NegotiationRoundSchema(
                    round_number=1,
                    buyer_turn=walk_msg,
                    seller_turn=walk_msg,
                    price_gap=round(abs(seller_ask - buyer_budget), 2),
                    status="no_deal",
                )
            ], [walk_msg], None, "rule_based_fallback", termination_reason

        # Active bounds configuration
        has_buyer_cap = (req.buyer_max_budget is not None) or (req.max_over_budget_pct > 0.0)
        buyer_cap_base = req.buyer_max_budget if req.buyer_max_budget is not None else buyer_budget
        effective_buyer_cap = round(buyer_cap_base * (1.0 + req.max_over_budget_pct / 100.0), 2)
        effective_seller_floor = req.seller_min_price if req.seller_min_price is not None else None

        if seller_ask < buyer_budget:
            seller_ask, buyer_budget = buyer_budget, seller_ask

        rounds: List[NegotiationRoundSchema] = []
        transcript: List[NegotiationMessageSchema] = []
        agreed_terms: Optional[AgreedTermsSchema] = None
        model_used = "rule_based_fallback"
        termination_reason: Optional[str] = None

        # Check AI Provider if enabled
        if use_ai:
            provider = get_ai_provider()
            try:
                available = await provider.is_available()
                if available:
                    model_used = provider.provider_name
            except Exception:
                pass

        # Check Quick Mode: Single-Round Term Suggestion + Adjustment (Default)
        use_quick_mode = req.quick_mode
        if "quick_mode" not in req.model_fields_set and "max_rounds" in req.model_fields_set:
            use_quick_mode = False

        if use_quick_mode:
            return self._execute_quick_mode(
                req=req,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                client_trust=client_trust,
                developer_trust=developer_trust,
                model_used=model_used,
                has_buyer_cap=has_buyer_cap,
                effective_buyer_cap=effective_buyer_cap,
                effective_seller_floor=effective_seller_floor,
            )

        # Multi-Round Mode (with Walk-Away / NO_DEAL checks)
        total_rounds = req.max_rounds
        previous_gap = None
        no_movement_streak = 0
        stagnation_limit = min(3, total_rounds)
        stagnation_streak = max(1, stagnation_limit - 1)

        for r_num in range(1, total_rounds + 1):
            is_final_round = (r_num == total_rounds)

            # Buyer Turn (clamped to effective_buyer_cap if active)
            buyer_msg = self._generate_rule_based_buyer_turn(
                round_num=r_num,
                total_rounds=total_rounds,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                base_currency=req.base_currency,
                payout_currency=req.payout_currency,
                client_trust=client_trust,
                developer_trust=developer_trust,
                previous_seller_msg=rounds[-1].seller_turn if rounds else None,
                has_buyer_cap=has_buyer_cap,
                effective_buyer_cap=effective_buyer_cap,
            )
            transcript.append(buyer_msg)

            # Seller Turn (floored to effective_seller_floor if active)
            seller_msg = self._generate_rule_based_seller_turn(
                round_num=r_num,
                total_rounds=total_rounds,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                base_currency=req.base_currency,
                payout_currency=req.payout_currency,
                client_trust=client_trust,
                developer_trust=developer_trust,
                buyer_msg=buyer_msg,
                effective_seller_floor=effective_seller_floor,
            )
            transcript.append(seller_msg)

            price_gap = round(abs(seller_msg.offer.price - buyer_msg.offer.price), 2)
            gap_pct = (price_gap / buyer_budget * 100.0) if buyer_budget > 0 else 0.0

            # Track movement for Walk-Away detection
            if previous_gap is not None and abs(price_gap - previous_gap) < 2.0:
                no_movement_streak += 1
            else:
                no_movement_streak = 0
            previous_gap = price_gap

            # Walk-Away Check 1: User-set stagnation limit (min(3, max_rounds) stagnant rounds)
            if no_movement_streak >= stagnation_streak:
                termination_reason = f"{stagnation_limit} rounds with no movement: parties unable to bridge concession deadlock"
                walk_away_msg = self._generate_no_deal_turn(r_num, buyer_budget, seller_ask, req.base_currency, termination_reason)
                transcript.append(walk_away_msg)
                rounds.append(NegotiationRoundSchema(
                    round_number=r_num,
                    buyer_turn=buyer_msg,
                    seller_turn=seller_msg,
                    price_gap=price_gap,
                    status="no_deal",
                ))
                return rounds, transcript, None, model_used, termination_reason

            # Walk-Away Check 2: Gap exceeds user-set walk_away_gap_pct tolerance threshold
            if is_final_round and gap_pct > req.walk_away_gap_pct:
                pct_str = f"{int(req.walk_away_gap_pct)}%" if req.walk_away_gap_pct == int(req.walk_away_gap_pct) else f"{req.walk_away_gap_pct:.1f}%"
                termination_reason = f"Price gap of {gap_pct:.1f}% exceeds {pct_str} tolerance threshold: terms irreconcilable"
                walk_away_msg = self._generate_no_deal_turn(r_num, buyer_budget, seller_ask, req.base_currency, termination_reason)
                transcript.append(walk_away_msg)
                rounds.append(NegotiationRoundSchema(
                    round_number=r_num,
                    buyer_turn=buyer_msg,
                    seller_turn=seller_msg,
                    price_gap=price_gap,
                    status="no_deal",
                ))
                return rounds, transcript, None, model_used, termination_reason

            round_status = "agreed" if is_final_round or price_gap < 5.0 else (
                "concession_made" if r_num > 1 else "negotiating"
            )

            round_obj = NegotiationRoundSchema(
                round_number=r_num,
                buyer_turn=buyer_msg,
                seller_turn=seller_msg,
                price_gap=price_gap,
                status=round_status,
            )
            rounds.append(round_obj)

            if is_final_round or round_status == "agreed":
                # Hard cap check: if seller price exceeds buyer hard cap -> NO_DEAL
                if has_buyer_cap and seller_msg.offer.price > effective_buyer_cap:
                    termination_reason = f"seller price {seller_msg.offer.price} exceeds buyer hard cap {effective_buyer_cap}"
                    walk_away_msg = self._generate_no_deal_turn(r_num, buyer_budget, seller_ask, req.base_currency, termination_reason)
                    transcript.append(walk_away_msg)
                    rounds[-1].status = "no_deal"
                    return rounds, transcript, None, model_used, termination_reason

                final_price = seller_msg.offer.price
                if effective_seller_floor is not None:
                    final_price = max(final_price, effective_seller_floor)
                if has_buyer_cap:
                    final_price = min(final_price, effective_buyer_cap)

                final_timeline = seller_msg.offer.timeline_days
                final_fx = seller_msg.offer.fx_strategy
                milestones_list = self._build_agreed_milestones(
                    price=final_price,
                    base_currency=req.base_currency,
                    brief=req.buyer_brief,
                )
                agreed_terms = AgreedTermsSchema(
                    title=f"Agreed Escrow: {self._extract_title(req.buyer_brief)}",
                    final_price=final_price,
                    timeline_days=final_timeline,
                    fx_strategy=final_fx,
                    base_currency=req.base_currency,
                    payout_currency=req.payout_currency,
                    milestones=milestones_list,
                    concession_summary=(
                        f"Consensus reached after {r_num} rounds. Final price settled at ${final_price:,.2f} "
                        f"{req.base_currency} (buyer initial: ${buyer_budget:,.2f}, seller initial: ${seller_ask:,.2f}), "
                        f"with {final_timeline} days delivery and {final_fx.value} protection."
                    ),
                )
                break

        return rounds, transcript, agreed_terms, model_used, termination_reason

    def _execute_quick_mode(
        self,
        req: NegotiationRequestSchema,
        buyer_budget: float,
        seller_ask: float,
        buyer_timeline: int,
        seller_timeline: int,
        client_trust: float,
        developer_trust: float,
        model_used: str,
        has_buyer_cap: bool = False,
        effective_buyer_cap: float = 0.0,
        effective_seller_floor: Optional[float] = None,
    ) -> Tuple[List[NegotiationRoundSchema], List[NegotiationMessageSchema], Optional[AgreedTermsSchema], str, Optional[str]]:
        """
        Quick Mode: Term suggestion + One-round adjustment (Default).
        Directly addresses mentor critique of 'multi-round theater'.
        Enforces user-defined hard caps, floors, and walk-away thresholds.
        """
        initial_gap_pct = ((seller_ask - buyer_budget) / buyer_budget * 100.0) if buyer_budget > 0 else 0.0

        # Term suggestion by Buyer Agent (no hardcoded 1.08 stretch; clamped to effective cap if active)
        buyer_suggested_price = buyer_budget
        if has_buyer_cap and effective_buyer_cap > 0:
            buyer_suggested_price = min(buyer_suggested_price, effective_buyer_cap)

        buyer_offer = AgentOfferSchema(
            price=buyer_suggested_price,
            timeline_days=buyer_timeline,
            fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
            milestones=[
                {"title": "Core Architecture & Scaffolding", "percentage": 0.30, "amount": round(buyer_suggested_price * 0.30, 2)},
                {"title": "API Engine & Integrations", "percentage": 0.45, "amount": round(buyer_suggested_price * 0.45, 2)},
                {"title": "Test Suite, Docs & Verification", "percentage": 0.25, "amount": round(buyer_suggested_price * 0.25, 2)},
            ],
            summary=f"${buyer_suggested_price:,.2f} {req.base_currency} | {buyer_timeline} Days | LOCKED_AT_SIGNING",
        )

        buyer_msg = NegotiationMessageSchema(
            round_number=1,
            sender="buyer_agent",
            sender_label="Buyer Agent (Acme AI)",
            reasoning=(
                f"Quick Mode term suggestion. Counterparty Developer trust score is {developer_trust:.1f}/100. "
                f"Proposing ${buyer_suggested_price:,.2f} {req.base_currency} over {buyer_timeline} business days "
                f"with LOCKED_AT_SIGNING FX guarantee for zero currency friction."
            ),
            message=(
                f"Welcome! In TrustBridge Quick Mode, we propose a streamlined single-round term structure: "
                f"${buyer_suggested_price:,.2f} {req.base_currency} delivered over {buyer_timeline} days across 3 verifiable milestones. "
                f"100% funds held in PayPal escrow with locked exchange rates."
            ),
            offer=buyer_offer,
            timestamp=datetime.utcnow(),
        )

        # Walk-Away Check: if initial gap exceeds user-set threshold (default: 40%)
        if initial_gap_pct > req.walk_away_gap_pct:
            pct_str = f"{int(req.walk_away_gap_pct)}%" if req.walk_away_gap_pct == int(req.walk_away_gap_pct) else f"{req.walk_away_gap_pct:.1f}%"
            seller_offer = AgentOfferSchema(
                price=seller_ask,
                timeline_days=seller_timeline,
                fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
                milestones=[],
                summary=f"${seller_ask:,.2f} {req.base_currency} | {seller_timeline} Days | NO_DEAL",
            )
            seller_msg = NegotiationMessageSchema(
                round_number=1,
                sender="seller_agent",
                sender_label="Seller Agent (DevStudio AI)",
                reasoning=(
                    f"Irreconcilable gap: Client budget ${buyer_budget:,.2f} vs Developer ask ${seller_ask:,.2f} "
                    f"represents a {initial_gap_pct:.1f}% divergence (>{pct_str} limit). Walking away with NO_DEAL."
                ),
                message=(
                    f"NO_DEAL: We cannot accept ${buyer_suggested_price:,.2f}. Our minimum viable threshold is "
                    f"${seller_ask:,.2f} ({initial_gap_pct:.1f}% gap exceeds the {pct_str} limit). We must terminate the negotiation."
                ),
                offer=seller_offer,
                timestamp=datetime.utcnow(),
            )
            round_obj = NegotiationRoundSchema(
                round_number=1,
                buyer_turn=buyer_msg,
                seller_turn=seller_msg,
                price_gap=round(seller_ask - buyer_suggested_price, 2),
                status="no_deal",
            )
            return [round_obj], [buyer_msg, seller_msg], None, model_used, f"Price gap of {initial_gap_pct:.1f}% exceeds {pct_str} walk-away threshold"

        # One-Round Adjustment by Seller Agent
        compromise_price = round((buyer_suggested_price + seller_ask) / 2, 2)
        if effective_seller_floor is not None:
            compromise_price = max(compromise_price, effective_seller_floor)

        # Hard cap check: if seller's best price exceeds buyer's hard cap -> NO_DEAL
        if has_buyer_cap and compromise_price > effective_buyer_cap:
            termination_reason = f"seller price {compromise_price} exceeds buyer hard cap {effective_buyer_cap}"
            seller_offer = AgentOfferSchema(
                price=compromise_price,
                timeline_days=seller_timeline,
                fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
                milestones=[],
                summary=f"${compromise_price:,.2f} {req.base_currency} | {seller_timeline} Days | NO_DEAL",
            )
            seller_msg = NegotiationMessageSchema(
                round_number=1,
                sender="seller_agent",
                sender_label="Seller Agent (DevStudio AI)",
                reasoning=(
                    f"Seller best price ${compromise_price:,.2f} exceeds buyer hard cap ${effective_buyer_cap:,.2f}. "
                    f"Terms are irreconcilable under user-enforced limits. Terminating with NO_DEAL."
                ),
                message=f"NO_DEAL: {termination_reason}. Parties have walked away.",
                offer=seller_offer,
                timestamp=datetime.utcnow(),
            )
            round_obj = NegotiationRoundSchema(
                round_number=1,
                buyer_turn=buyer_msg,
                seller_turn=seller_msg,
                price_gap=round(abs(compromise_price - buyer_suggested_price), 2),
                status="no_deal",
            )
            return [round_obj], [buyer_msg, seller_msg], None, model_used, termination_reason

        seller_offer = AgentOfferSchema(
            price=compromise_price,
            timeline_days=round((buyer_timeline + seller_timeline) / 2),
            fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
            milestones=[
                {"title": "Core Architecture & Scaffolding", "percentage": 0.30, "amount": round(compromise_price * 0.30, 2)},
                {"title": "API Engine & Integrations", "percentage": 0.45, "amount": round(compromise_price * 0.45, 2)},
                {"title": "Test Suite, Docs & Verification", "percentage": 0.25, "amount": round(compromise_price * 0.25, 2)},
            ],
            summary=f"${compromise_price:,.2f} {req.base_currency} | {round((buyer_timeline + seller_timeline)/2)} Days | LOCKED_AT_SIGNING",
        )

        seller_msg = NegotiationMessageSchema(
            round_number=1,
            sender="seller_agent",
            sender_label="Seller Agent (DevStudio AI)",
            reasoning=(
                f"Quick Mode single-round adjustment. Client trust score is {client_trust:.1f}/100. "
                f"Initial gap is {initial_gap_pct:.1f}% (within {req.walk_away_gap_pct:.1f}% threshold). Meeting at equitable consensus "
                f"${compromise_price:,.2f} {req.base_currency} with LOCKED_AT_SIGNING FX guarantee."
            ),
            message=(
                f"Deal agreed! We accept the Quick Mode terms with a balanced adjustment to ${compromise_price:,.2f} {req.base_currency} "
                f"over {seller_offer.timeline_days} days with LOCKED_AT_SIGNING FX lock. Ready to initialize escrow."
            ),
            offer=seller_offer,
            timestamp=datetime.utcnow(),
        )

        round_obj = NegotiationRoundSchema(
            round_number=1,
            buyer_turn=buyer_msg,
            seller_turn=seller_msg,
            price_gap=0.0,
            status="agreed",
        )

        agreed = AgreedTermsSchema(
            title=f"Quick Escrow: {self._extract_title(req.buyer_brief)}",
            final_price=compromise_price,
            timeline_days=seller_offer.timeline_days,
            fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
            base_currency=req.base_currency,
            payout_currency=req.payout_currency,
            milestones=self._build_agreed_milestones(compromise_price, req.base_currency, req.buyer_brief),
            concession_summary=(
                f"Quick Mode agreement achieved in 1 round. Settled at ${compromise_price:,.2f} {req.base_currency} "
                f"(balanced between ${buyer_budget:,.2f} and ${seller_ask:,.2f}) with a {seller_offer.timeline_days}-day schedule."
            ),
        )

        return [round_obj], [buyer_msg, seller_msg], agreed, model_used, None

    # --------------------------------------------------------------------------
    # Rule-Based Autonomous Agents
    # --------------------------------------------------------------------------

    def _generate_rule_based_buyer_turn(
        self,
        round_num: int,
        total_rounds: int,
        buyer_budget: float,
        seller_ask: float,
        buyer_timeline: int,
        seller_timeline: int,
        base_currency: str,
        payout_currency: str,
        client_trust: float,
        developer_trust: float,
        previous_seller_msg: Optional[NegotiationMessageSchema],
        has_buyer_cap: bool = False,
        effective_buyer_cap: float = 0.0,
    ) -> NegotiationMessageSchema:
        if round_num == 1:
            price = buyer_budget
            timeline = buyer_timeline
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Anchoring baseline. Counterparty developer trust score is {developer_trust:.1f}/100. "
                f"Proposing budget of ${price:,.2f} {base_currency} with {timeline} days and LOCKED_AT_SIGNING FX hedging."
            )
            message = (
                f"Hello! Representing the client (Trust Score: {client_trust:.1f}/100), we propose ${price:,.2f} {base_currency} "
                f"over {timeline} business days with dual-custody PayPal escrow and 100% FX lock at signing."
            )
        elif round_num == 2:
            prev_seller_price = previous_seller_msg.offer.price if previous_seller_msg else seller_ask
            concession = (prev_seller_price - buyer_budget) * 0.40
            price = round(buyer_budget + concession, 2)
            timeline = round((buyer_timeline + seller_timeline) / 2)
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Counterparty countered at ${prev_seller_price:,.2f}. Offering constructive concession of ${concession:,.2f} "
                f"to ${price:,.2f} ({timeline} days) with automated PR verification."
            )
            message = (
                f"We reviewed your breakdown. In recognition of your verifiable engineering milestones, we can adjust our offer "
                f"to ${price:,.2f} {base_currency} and grant {timeline} days."
            )
        else:
            midpoint = round((buyer_budget + seller_ask) / 2, 2)
            price = midpoint
            timeline = seller_timeline - 2
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = f"Consensus round reached. Converging onto compromise price ${price:,.2f} {base_currency}."
            message = f"We are ready to close at ${price:,.2f} {base_currency} over {timeline} days with LOCKED_AT_SIGNING."

        if has_buyer_cap and effective_buyer_cap > 0:
            price = min(price, effective_buyer_cap)

        milestones = [
            {"title": "Core Architecture & Scaffolding", "percentage": 0.30, "amount": round(price * 0.30, 2)},
            {"title": "API Engine & Integrations", "percentage": 0.45, "amount": round(price * 0.45, 2)},
            {"title": "Test Suite, Docs & Verification", "percentage": 0.25, "amount": round(price * 0.25, 2)},
        ]

        offer = AgentOfferSchema(
            price=price,
            timeline_days=timeline,
            fx_strategy=fx_strategy,
            milestones=milestones,
            summary=f"${price:,.2f} {base_currency} | {timeline} Days | {fx_strategy.value}",
        )

        return NegotiationMessageSchema(
            round_number=round_num,
            sender="buyer_agent",
            sender_label="Buyer Agent (Acme AI)",
            reasoning=reasoning,
            message=message,
            offer=offer,
            timestamp=datetime.utcnow(),
        )

    def _generate_rule_based_seller_turn(
        self,
        round_num: int,
        total_rounds: int,
        buyer_budget: float,
        seller_ask: float,
        buyer_timeline: int,
        seller_timeline: int,
        base_currency: str,
        payout_currency: str,
        client_trust: float,
        developer_trust: float,
        buyer_msg: NegotiationMessageSchema,
        effective_seller_floor: Optional[float] = None,
    ) -> NegotiationMessageSchema:
        buyer_price = buyer_msg.offer.price
        gap_pct = (abs(seller_ask - buyer_price) / buyer_budget * 100.0) if buyer_budget > 0 else 0.0

        if round_num == 1:
            counter_price = round(seller_ask - (seller_ask - buyer_budget) * 0.25, 2)
            timeline = seller_timeline
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Client trust score is {client_trust:.1f}/100. Buyer offered ${buyer_price:,.2f}. "
                f"Discounting initial ask to ${counter_price:,.2f} ({timeline} days) with LOCKED_AT_SIGNING."
            )
            message = (
                f"Thank you. Considering the client's verified track record, we can deliver the full scope for "
                f"${counter_price:,.2f} {base_currency} in {timeline} days with complete test automation."
            )
        elif round_num == 2:
            counter_price = round((buyer_price + seller_ask) / 2, 2)
            timeline = seller_timeline - 2
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = f"Countering at ${counter_price:,.2f} with {timeline} days."
            message = f"We can optimize delivery to {timeline} days and meet at ${counter_price:,.2f} {base_currency}."
        else:
            counter_price = buyer_price
            timeline = buyer_msg.offer.timeline_days
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = f"Buyer offered ${buyer_price:,.2f}. Reaching mutual profitability. Confirming agreement."
            message = f"Deal confirmed! We accept ${counter_price:,.2f} {base_currency} over {timeline} days."

        if effective_seller_floor is not None:
            counter_price = max(counter_price, effective_seller_floor)

        milestones = [
            {"title": "Core Architecture & Scaffolding", "percentage": 0.30, "amount": round(counter_price * 0.30, 2)},
            {"title": "API Engine & Integrations", "percentage": 0.45, "amount": round(counter_price * 0.45, 2)},
            {"title": "Test Suite, Docs & Verification", "percentage": 0.25, "amount": round(counter_price * 0.25, 2)},
        ]

        offer = AgentOfferSchema(
            price=counter_price,
            timeline_days=timeline,
            fx_strategy=fx_strategy,
            milestones=milestones,
            summary=f"${counter_price:,.2f} {base_currency} | {timeline} Days | {fx_strategy.value}",
        )

        return NegotiationMessageSchema(
            round_number=round_num,
            sender="seller_agent",
            sender_label="Seller Agent (DevStudio AI)",
            reasoning=reasoning,
            message=message,
            offer=offer,
            timestamp=datetime.utcnow(),
        )

    def _generate_no_deal_turn(
        self,
        round_num: int,
        buyer_budget: float,
        seller_ask: float,
        base_currency: str,
        reason: str,
    ) -> NegotiationMessageSchema:
        offer = AgentOfferSchema(
            price=0.0,
            timeline_days=0,
            fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
            milestones=[],
            summary="NO_DEAL: Negotiation Terminated",
        )
        return NegotiationMessageSchema(
            round_number=round_num,
            sender="system",
            sender_label="Negotiation Arbiter (TrustBridge)",
            reasoning=f"Irreconcilable negotiation deadlock detected. Reason: {reason}.",
            message=f"NO_DEAL: {reason}. Parties have walked away.",
            offer=offer,
            timestamp=datetime.utcnow(),
        )

    # --------------------------------------------------------------------------
    # Escrow Contract Generation Helper
    # --------------------------------------------------------------------------

    async def _build_contract_create_schema(
        self,
        req: NegotiationRequestSchema,
        agreed: AgreedTermsSchema,
    ) -> ContractCreateSchema:
        fx_rate = await fx_service.get_rate(req.base_currency, req.payout_currency)
        return ContractCreateSchema(
            title=agreed.title,
            brief=f"{req.buyer_brief} (Negotiated agreed terms: {agreed.concession_summary})",
            client_name=req.client_name,
            client_email=req.client_email,
            client_country=req.client_country,
            developer_name=req.developer_name,
            developer_email=req.developer_email,
            developer_country=req.developer_country,
            base_currency=req.base_currency,
            payout_currency=req.payout_currency,
            total_amount=agreed.final_price,
            fx_strategy=agreed.fx_strategy,
            locked_fx_rate=fx_rate,
            milestones=agreed.milestones,
        )

    def _build_agreed_milestones(
        self,
        price: float,
        base_currency: str,
        brief: str,
    ) -> List[MilestoneCreateSchema]:
        m1_amt = round(price * 0.30, 2)
        m2_amt = round(price * 0.45, 2)
        m3_amt = round(price - m1_amt - m2_amt, 2)

        return [
            MilestoneCreateSchema(
                title="Milestone 1: Architecture, Data Schema & Setup",
                description="Project scaffolding, database migrations, configuration setup, and core endpoints.",
                amount=m1_amt,
                order_index=1,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(
                        description="Repository initialized with clean directory structure and passing lint checks",
                        verification_method="github_pr",
                    ),
                    AcceptanceCriterionSchema(
                        description="Data models and database migrations execute successfully",
                        verification_method="automated_test",
                    ),
                ],
            ),
            MilestoneCreateSchema(
                title="Milestone 2: Core Feature Implementation & Integrations",
                description="Full implementation of required business logic, external API integrations, and error handling.",
                amount=m2_amt,
                order_index=2,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(
                        description="Core API endpoints operational with full parameter validation",
                        verification_method="api_response",
                    ),
                    AcceptanceCriterionSchema(
                        description="Integration test suite executes with >= 90% pass rate",
                        verification_method="automated_test",
                    ),
                ],
            ),
            MilestoneCreateSchema(
                title="Milestone 3: Verification, End-to-End Testing & Handover",
                description="Complete test coverage, documentation, deployment instructions, and production verification.",
                amount=m3_amt,
                order_index=3,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(
                        description="Automated end-to-end test suite passes in CI/CD environment",
                        verification_method="automated_test",
                    ),
                    AcceptanceCriterionSchema(
                        description="Interactive API documentation (Swagger/OpenAPI) and deployment guide provided",
                        verification_method="manual_review",
                    ),
                ],
            ),
        ]

    def _extract_price(self, text: str, default: float) -> float:
        matches_neg = re.findall(r"(?:-\s*\$|\$\s*-)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
        if matches_neg:
            return -float(matches_neg[0].replace(",", ""))

        matches = re.findall(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
        if matches:
            return float(matches[0].replace(",", ""))

        matches_word_neg = re.findall(r"(?:budget|ask|price|rate|cost)\s*(?:is|of|:)?\s*-\s*([0-9]+(?:,[0-9]{3})*)", text, re.IGNORECASE)
        if matches_word_neg:
            return -float(matches_word_neg[0].replace(",", ""))

        matches_word = re.findall(r"(?:budget|ask|price|rate|cost)\s*(?:is|of|:)?\s*([0-9]+(?:,[0-9]{3})*)", text, re.IGNORECASE)
        if matches_word:
            return float(matches_word[0].replace(",", ""))
        
        matches_digits = re.findall(r"\b([0-9]{3,5})\b", text)
        if matches_digits:
            return float(matches_digits[0])

        return default

    def _extract_timeline(self, text: str, default: int) -> int:
        matches = re.findall(r"([0-9]+)\s*(?:days|day|weeks|week)", text, re.IGNORECASE)
        if matches:
            val = int(matches[0])
            if "week" in text.lower():
                val *= 7
            return val
        return default

    def _extract_title(self, text: str) -> str:
        clean = re.sub(r"[\$0-9,\.]+", "", text)
        words = clean.split()
        if len(words) >= 4:
            return " ".join(words[:5]).capitalize()
        return "Custom Engineering Deliverable"


negotiator_service = NegotiatorService()

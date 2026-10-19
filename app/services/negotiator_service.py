import re
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import FXStrategy, VerificationType
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
)
from app.services.contract_service import contract_service
from app.services.fx_service import fx_service


class NegotiatorService:
    """
    Autonomous Agent-to-Agent Negotiation Engine.
    Orchestrates multi-round negotiations between a Buyer Agent and a Seller Agent
    over price, milestones, timeline, and FX risk strategy.
    
    Uses local Ollama (llama3.2:3b) with prompt engineering and structured JSON output,
    with a deterministic rule-based fallback when offline or during testing.
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
        
        # Determine whether to attempt Ollama
        use_ollama = (
            not req.force_rule_based
            and self.settings.ENVIRONMENT != "testing"
        )
        
        # 1. Execute Multi-Round Negotiation
        rounds, transcript, agreed_terms, model_used = await self._execute_negotiation_rounds(
            req=req,
            use_ollama=use_ollama,
        )

        status = "agreed" if agreed_terms is not None else "failed"
        contract_id = None
        created_contract = None

        # 2. Auto-create TrustBridge Escrow Contract if agreed and requested
        if agreed_terms and req.auto_create_contract:
            contract_create_req = await self._build_contract_create_schema(req, agreed_terms)
            try:
                created_contract = await contract_service.create_contract(db, contract_create_req)
                contract_id = created_contract.id
            except Exception as e:
                # Log or keep agreement record even if creation had issue
                pass

        response = NegotiationResponseSchema(
            id=negotiation_id,
            status=status,
            total_rounds=len(rounds),
            transcript=transcript,
            rounds=rounds,
            agreed_terms=agreed_terms,
            contract_id=contract_id,
            contract=created_contract,
            model_used=model_used,
            created_at=datetime.utcnow(),
        )

        # Store in cache
        self._history[negotiation_id] = response
        return response

    def get_negotiation(self, negotiation_id: str) -> Optional[NegotiationResponseSchema]:
        return self._history.get(negotiation_id)

    def list_negotiations(self) -> List[NegotiationResponseSchema]:
        return sorted(self._history.values(), key=lambda n: n.created_at, reverse=True)

    # --------------------------------------------------------------------------
    # Multi-Round Execution Core
    # --------------------------------------------------------------------------

    async def _execute_negotiation_rounds(
        self,
        req: NegotiationRequestSchema,
        use_ollama: bool,
    ) -> Tuple[List[NegotiationRoundSchema], List[NegotiationMessageSchema], Optional[AgreedTermsSchema], str]:
        buyer_budget = self._extract_price(req.buyer_brief, default=3000.0)
        seller_ask = self._extract_price(req.seller_brief, default=4000.0)
        
        # Ensure seller ask is at least equal to buyer budget
        if seller_ask < buyer_budget:
            seller_ask, buyer_budget = buyer_budget, seller_ask

        buyer_timeline = self._extract_timeline(req.buyer_brief, default=21)
        seller_timeline = self._extract_timeline(req.seller_brief, default=28)

        rounds: List[NegotiationRoundSchema] = []
        transcript: List[NegotiationMessageSchema] = []
        agreed_terms: Optional[AgreedTermsSchema] = None
        model_used = "rule_based_fallback"

        # Try Ollama if enabled
        if use_ollama:
            try:
                ollama_ok = await self._check_ollama_available()
                if ollama_ok:
                    res = await self._run_ollama_rounds(
                        req=req,
                        buyer_budget=buyer_budget,
                        seller_ask=seller_ask,
                        buyer_timeline=buyer_timeline,
                        seller_timeline=seller_timeline,
                    )
                    if res:
                        return res[0], res[1], res[2], "llama3.2:3b"
            except Exception:
                # Fall back seamlessly
                pass

        # Deterministic Negotiation Engine
        total_rounds = req.max_rounds
        for r_num in range(1, total_rounds + 1):
            is_final_round = (r_num == total_rounds)

            # --- Buyer Turn ---
            buyer_msg = self._generate_rule_based_buyer_turn(
                round_num=r_num,
                total_rounds=total_rounds,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                base_currency=req.base_currency,
                payout_currency=req.payout_currency,
                previous_seller_msg=rounds[-1].seller_turn if rounds else None,
            )
            transcript.append(buyer_msg)

            # --- Seller Turn ---
            seller_msg = self._generate_rule_based_seller_turn(
                round_num=r_num,
                total_rounds=total_rounds,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                base_currency=req.base_currency,
                payout_currency=req.payout_currency,
                buyer_msg=buyer_msg,
            )
            transcript.append(seller_msg)

            price_gap = abs(seller_msg.offer.price - buyer_msg.offer.price)
            round_status = "agreed" if is_final_round or price_gap < 1.0 else (
                "concession_made" if r_num > 1 else "negotiating"
            )

            round_obj = NegotiationRoundSchema(
                round_number=r_num,
                buyer_turn=buyer_msg,
                seller_turn=seller_msg,
                price_gap=round(price_gap, 2),
                status=round_status,
            )
            rounds.append(round_obj)

            if is_final_round or round_status == "agreed":
                # Build agreed terms
                final_price = seller_msg.offer.price
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
                        f"{req.base_currency} (compromise between initial ${buyer_budget:,.2f} and ${seller_ask:,.2f}), "
                        f"with a {final_timeline}-day timeline and {final_fx.value} FX hedging."
                    ),
                )
                break

        return rounds, transcript, agreed_terms, model_used

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
        previous_seller_msg: Optional[NegotiationMessageSchema],
    ) -> NegotiationMessageSchema:
        if round_num == 1:
            price = buyer_budget
            timeline = buyer_timeline
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Establishing baseline anchor at buyer's stated budget of ${buyer_budget:,.2f} {base_currency} "
                f"with a strict {timeline}-day target. Proposing LOCKED_AT_SIGNING FX strategy to remove currency volatility "
                f"from our escrow calculation."
            )
            message = (
                f"Hello! On behalf of the client, we propose a total contract value of ${price:,.2f} {base_currency} "
                f"delivered across 3 verifiable milestones within {timeline} business days. "
                f"All funds will be held in PayPal dual-custody escrow with 100% FX lock at signing."
            )
        elif round_num == 2:
            prev_seller_price = previous_seller_msg.offer.price if previous_seller_msg else seller_ask
            # Offer concession 40% toward seller price
            concession = (prev_seller_price - buyer_budget) * 0.40
            price = round(buyer_budget + concession, 2)
            timeline = round((buyer_timeline + seller_timeline) / 2)
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Seller countered at ${prev_seller_price:,.2f}. To show good faith, increasing budget offer by "
                f"${concession:,.2f} to ${price:,.2f} and extending timeline to {timeline} days, provided seller guarantees "
                f"automated test verification and PayPal webhook integration."
            )
            message = (
                f"We reviewed your breakdown. In recognition of your comprehensive testing harness and backend architecture, "
                f"we can increase our offer to ${price:,.2f} {base_currency} and grant {timeline} days. "
                f"We require full PR verification and test logs before escrow milestone release."
            )
        else:
            # Final round: converge to compromise midpoint
            prev_seller_price = previous_seller_msg.offer.price if previous_seller_msg else seller_ask
            midpoint = round((buyer_budget + seller_ask) / 2, 2)
            price = midpoint
            timeline = seller_timeline - 2
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Consensus round reached. Converging onto equitable midpoint ${price:,.2f} {base_currency} "
                f"with {timeline} days delivery. This satisfies both parties' reservation constraints."
            )
            message = (
                f"We are prepared to close at our consensus target of ${price:,.2f} {base_currency} over a {timeline}-day "
                f"timeline with LOCKED_AT_SIGNING FX protection. If you agree, we will immediately initialize the TrustBridge escrow contract."
            )

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
            sender_label="Buyer Agent (Acme Corp AI)",
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
        buyer_msg: NegotiationMessageSchema,
    ) -> NegotiationMessageSchema:
        buyer_price = buyer_msg.offer.price
        
        if round_num == 1:
            # Discount initial ask by 25% of difference
            counter_price = round(seller_ask - (seller_ask - buyer_budget) * 0.25, 2)
            timeline = seller_timeline
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Buyer opened at ${buyer_price:,.2f}. Our initial ask is ${seller_ask:,.2f}. "
                f"Discounting initial ask to ${counter_price:,.2f} ({timeline} days) to account for full production-readiness, "
                f"test suites, and edge case handling."
            )
            message = (
                f"Thank you for the proposal. Given the technical complexity, PayPal webhook handling, and cryptographic verification, "
                f"${buyer_price:,.2f} is below our operational threshold. However, we want to make this work: "
                f"we can deliver the entire scope for ${counter_price:,.2f} {base_currency} in {timeline} days."
            )
        elif round_num == 2:
            # Move closer to midpoint
            counter_price = round((buyer_price + seller_ask) / 2, 2)
            timeline = seller_timeline - 2
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Buyer made a constructive concession to ${buyer_price:,.2f}. Countering at ${counter_price:,.2f} "
                f"with {timeline} days. This preserves healthy developer margin while fitting client's updated ceiling."
            )
            message = (
                f"We appreciate the upward adjustment to ${buyer_price:,.2f}. We can optimize our sprint schedule to {timeline} days "
                f"and meet you at ${counter_price:,.2f} {base_currency}. We will include full automated unit/integration test suites "
                f"verifiable via GitHub pull request."
            )
        else:
            # Final consensus agreement
            counter_price = buyer_price  # agree on buyer's converged price
            timeline = buyer_msg.offer.timeline_days
            fx_strategy = FXStrategy.LOCKED_AT_SIGNING
            reasoning = (
                f"Buyer offered ${buyer_price:,.2f} on {timeline} days. This reaches mutual profitability and covers all "
                f"engineering overhead with zero currency slippage via LOCKED_AT_SIGNING. Confirming agreement."
            )
            message = (
                f"Deal confirmed! We accept ${counter_price:,.2f} {base_currency} on a {timeline}-day timeline with "
                f"LOCKED_AT_SIGNING FX guarantee. Let's initialize the escrow contract and begin Milestone 1."
            )

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
            sender_label="Seller Agent (DevStudio Lead AI)",
            reasoning=reasoning,
            message=message,
            offer=offer,
            timestamp=datetime.utcnow(),
        )

    # --------------------------------------------------------------------------
    # Ollama LLM Multi-Round Orchestration
    # --------------------------------------------------------------------------

    async def _check_ollama_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.settings.OLLAMA_BASE_URL}/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    async def _run_ollama_rounds(
        self,
        req: NegotiationRequestSchema,
        buyer_budget: float,
        seller_ask: float,
        buyer_timeline: int,
        seller_timeline: int,
    ) -> Optional[Tuple[List[NegotiationRoundSchema], List[NegotiationMessageSchema], Optional[AgreedTermsSchema]]]:
        rounds: List[NegotiationRoundSchema] = []
        transcript: List[NegotiationMessageSchema] = []
        agreed_terms: Optional[AgreedTermsSchema] = None
        total_rounds = req.max_rounds

        for r_num in range(1, total_rounds + 1):
            is_final = (r_num == total_rounds)
            
            # 1. Buyer Turn via Ollama
            buyer_msg = await self._query_ollama_agent(
                role="buyer",
                round_num=r_num,
                total_rounds=total_rounds,
                buyer_brief=req.buyer_brief,
                seller_brief=req.seller_brief,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                base_currency=req.base_currency,
                transcript=transcript,
            )
            if not buyer_msg:
                # Fallback if Ollama times out or errors
                return None
            transcript.append(buyer_msg)

            # 2. Seller Turn via Ollama
            seller_msg = await self._query_ollama_agent(
                role="seller",
                round_num=r_num,
                total_rounds=total_rounds,
                buyer_brief=req.buyer_brief,
                seller_brief=req.seller_brief,
                buyer_budget=buyer_budget,
                seller_ask=seller_ask,
                buyer_timeline=buyer_timeline,
                seller_timeline=seller_timeline,
                base_currency=req.base_currency,
                transcript=transcript,
            )
            if not seller_msg:
                return None
            transcript.append(seller_msg)

            price_gap = abs(seller_msg.offer.price - buyer_msg.offer.price)
            round_status = "agreed" if is_final or price_gap < 1.0 else (
                "concession_made" if r_num > 1 else "negotiating"
            )

            round_obj = NegotiationRoundSchema(
                round_number=r_num,
                buyer_turn=buyer_msg,
                seller_turn=seller_msg,
                price_gap=round(price_gap, 2),
                status=round_status,
            )
            rounds.append(round_obj)

            if is_final or round_status == "agreed":
                final_price = seller_msg.offer.price
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
                        f"Autonomous Ollama AI agreement achieved in {r_num} rounds. Final price: "
                        f"${final_price:,.2f} {req.base_currency}, timeline: {final_timeline} days, FX: {final_fx.value}."
                    ),
                )
                break

        return rounds, transcript, agreed_terms

    async def _query_ollama_agent(
        self,
        role: str,
        round_num: int,
        total_rounds: int,
        buyer_brief: str,
        seller_brief: str,
        buyer_budget: float,
        seller_ask: float,
        buyer_timeline: int,
        seller_timeline: int,
        base_currency: str,
        transcript: List[NegotiationMessageSchema],
    ) -> Optional[NegotiationMessageSchema]:
        is_buyer = (role == "buyer")
        is_final = (round_num == total_rounds)
        
        system_prompt = (
            f"You are the autonomous {'BUYER' if is_buyer else 'SELLER'} negotiation agent for TrustBridge. "
            f"Your goal is to negotiate terms (price, timeline, FX risk, and milestones) with the counterparty. "
            f"You must return ONLY raw valid JSON conforming to this schema:\n"
            f'{{"reasoning": "your private strategic thought process", '
            f'"message": "polite, professional negotiation message to other agent", '
            f'"price": 3400.0, "timeline_days": 24, "fx_strategy": "LOCKED_AT_SIGNING"}}\n'
            f"Do not include markdown backticks or commentary outside the JSON."
        )

        history_summary = "\n".join(
            f"- Round {m.round_number} [{m.sender_label}]: Price ${m.offer.price:,.2f}, Timeline {m.offer.timeline_days}d. Msg: {m.message}"
            for m in transcript
        ) if transcript else "No previous rounds yet."

        user_content = f"""
Current Round: {round_num} of {total_rounds} (Final round: {is_final})
Buyer Brief: {buyer_brief} (Initial budget: ${buyer_budget:,.2f} {base_currency}, Target timeline: {buyer_timeline}d)
Seller Brief: {seller_brief} (Initial ask: ${seller_ask:,.2f} {base_currency}, Delivery timeline: {seller_timeline}d)
Negotiation History So Far:
{history_summary}

Instructions for {'BUYER' if is_buyer else 'SELLER'}:
- In round {round_num}, {'make a reasonable compromise or accept deal if close' if is_final else 'negotiate toward mutual value'}.
- Available fx_strategy values: "LOCKED_AT_SIGNING", "SPLIT_RISK", "AT_SETTLEMENT".
- Return pure valid JSON.
"""
        payload = {
            "model": "llama3.2:3b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.4},
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(f"{self.settings.OLLAMA_BASE_URL}/api/chat", json=payload)
                if res.status_code != 200:
                    return None
                data = res.json()
                content = data.get("message", {}).get("content", "").strip()
                parsed = json.loads(content)

                price = float(parsed.get("price", buyer_budget if is_buyer else seller_ask))
                timeline_days = int(parsed.get("timeline_days", buyer_timeline if is_buyer else seller_timeline))
                fx_str = parsed.get("fx_strategy", "LOCKED_AT_SIGNING")
                fx_strategy = FXStrategy(fx_str) if fx_str in FXStrategy._value2member_map_ else FXStrategy.LOCKED_AT_SIGNING
                reasoning = parsed.get("reasoning", "Negotiating optimal terms for escrow contract.")
                message = parsed.get("message", f"We propose ${price:,.2f} over {timeline_days} days.")

                offer = AgentOfferSchema(
                    price=price,
                    timeline_days=timeline_days,
                    fx_strategy=fx_strategy,
                    milestones=[],
                    summary=f"${price:,.2f} {base_currency} | {timeline_days} Days | {fx_strategy.value}",
                )

                return NegotiationMessageSchema(
                    round_number=round_num,
                    sender="buyer_agent" if is_buyer else "seller_agent",
                    sender_label="Buyer Agent (Acme AI)" if is_buyer else "Seller Agent (DevStudio AI)",
                    reasoning=reasoning,
                    message=message,
                    offer=offer,
                    timestamp=datetime.utcnow(),
                )
        except Exception:
            return None

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

    # --------------------------------------------------------------------------
    # Extraction Helpers
    # --------------------------------------------------------------------------

    def _extract_price(self, text: str, default: float) -> float:
        # Match $3,000, $3000, 3000 USD, budget 3000, ask 4000
        matches = re.findall(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
        if matches:
            return float(matches[0].replace(",", ""))
        
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

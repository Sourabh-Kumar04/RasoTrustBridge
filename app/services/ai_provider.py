import os
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any
import httpx

from app.config import get_settings
from app.models.enums import FXStrategy, VerificationType
from app.schemas.ai import (
    AIBriefRequestSchema,
    AIMilestoneSuggestionSchema,
    AIContractResponseSchema,
)
from app.schemas.negotiation import (
    NegotiationMessageSchema,
    AgentOfferSchema,
)


class AIProvider(ABC):
    """
    Abstract AI Provider Interface for TrustBridge Contract Builder & Autonomous Negotiator.
    Supports swappable model backends: zero-config local Ollama (default) or OpenAI.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        pass

    @abstractmethod
    async def generate_contract(self, req: AIBriefRequestSchema) -> Optional[AIContractResponseSchema]:
        pass

    @abstractmethod
    async def query_negotiator_agent(
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
        client_trust_score: float,
        developer_trust_score: float,
        transcript: List[NegotiationMessageSchema],
    ) -> Optional[NegotiationMessageSchema]:
        pass


class OllamaProvider(AIProvider):
    """
    Local Ollama AI Provider (llama3.2:3b).
    Zero-config local model backend.
    """

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        settings = get_settings()
        self.base_url = base_url or settings.OLLAMA_BASE_URL
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = settings.OLLAMA_TIMEOUT_SECONDS

    @property
    def provider_name(self) -> str:
        return f"ollama:{self.model}"

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    async def generate_contract(self, req: AIBriefRequestSchema) -> Optional[AIContractResponseSchema]:
        url = f"{self.base_url}/api/generate"
        expected_budget = req.budget or 1200.0
        system_prompt = (
            "You are TrustBridge AI: an expert escrow contract architect and payments engineer. "
            "Convert the user's project brief into a structured, production-ready freelance contract. "
            "You must return ONLY raw valid JSON conforming to the requested schema. Do not add markdown backticks."
        )
        user_content = f"""
Project Brief: {req.brief}
Client: {req.client_name} ({req.client_country})
Developer: {req.developer_name} ({req.developer_country})
Budget: {expected_budget} {req.base_currency}
Base Currency: {req.base_currency}
Payout Currency: {req.payout_currency}
FX Strategy: {req.fx_strategy.value if req.fx_strategy else 'LOCKED_AT_SIGNING'}

Return valid JSON with title, summary, suggested_total_amount, base_currency, payout_currency, recommended_fx_strategy, fx_strategy_rationale, dispute_protocol_summary, and milestones.
"""
        payload = {
            "model": self.model,
            "prompt": f"{system_prompt}\n\n{user_content}",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    response_text = data.get("response", "").strip()
                    if response_text.startswith("```"):
                        response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
                        response_text = re.sub(r"\n?```$", "", response_text)
                    parsed = json.loads(response_text)
                    return self._parse_contract_json(parsed, req)
        except Exception:
            pass
        return None

    async def query_negotiator_agent(
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
        client_trust_score: float,
        developer_trust_score: float,
        transcript: List[NegotiationMessageSchema],
    ) -> Optional[NegotiationMessageSchema]:
        is_buyer = (role == "buyer")
        is_final = (round_num == total_rounds)

        system_prompt = (
            f"You are the autonomous {'BUYER' if is_buyer else 'SELLER'} negotiation agent for TrustBridge. "
            f"Counterparty trust score is {developer_trust_score if is_buyer else client_trust_score}/100. "
            f"Return ONLY raw valid JSON: {{\"reasoning\": \"str\", \"message\": \"str\", \"price\": 3500.0, \"timeline_days\": 21, \"fx_strategy\": \"LOCKED_AT_SIGNING\"}}"
        )

        history_summary = "\n".join(
            f"- Round {m.round_number} [{m.sender_label}]: Price ${m.offer.price:,.2f}. Msg: {m.message}"
            for m in transcript
        ) if transcript else "No previous rounds yet."

        user_content = f"""
Current Round: {round_num} of {total_rounds} (Final: {is_final})
Buyer Brief: {buyer_brief} (Initial budget: ${buyer_budget:,.2f})
Seller Brief: {seller_brief} (Initial ask: ${seller_ask:,.2f})
History:
{history_summary}
Instructions: Negotiate terms realistically. In final round or quick mode, converge if close.
"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.3},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(f"{self.base_url}/api/chat", json=payload)
                if res.status_code == 200:
                    content = res.json().get("message", {}).get("content", "").strip()
                    parsed = json.loads(content)
                    return self._build_agent_message(parsed, role, round_num, buyer_budget, seller_ask, buyer_timeline, seller_timeline, base_currency)
        except Exception:
            pass
        return None

    def _parse_contract_json(self, parsed: Dict[str, Any], req: AIBriefRequestSchema) -> Optional[AIContractResponseSchema]:
        raw_milestones = parsed.get("milestones", [])
        if len(raw_milestones) < 2:
            return None
        total = float(req.budget or parsed.get("suggested_total_amount", 1200.0))
        milestones = []
        for m in raw_milestones:
            pct = float(m.get("amount_percentage", 0.25))
            milestones.append(
                AIMilestoneSuggestionSchema(
                    order_index=int(m.get("order_index", 1)),
                    title=m.get("title", "Milestone"),
                    description=m.get("description", ""),
                    amount_percentage=pct,
                    estimated_amount=round(float(m.get("estimated_amount", total * pct)), 2),
                    verification_type=VerificationType(m.get("verification_type", "CODE_DELIVERY")),
                    acceptance_criteria=m.get("acceptance_criteria", []),
                )
            )
        strategy = req.fx_strategy or FXStrategy.LOCKED_AT_SIGNING
        return AIContractResponseSchema(
            title=parsed.get("title", "Freelance Software Contract"),
            summary=parsed.get("summary", req.brief[:120]),
            suggested_total_amount=total,
            base_currency=req.base_currency,
            payout_currency=req.payout_currency,
            recommended_fx_strategy=strategy,
            fx_strategy_rationale=parsed.get("fx_strategy_rationale", "Locks exchange rate at agreement signing."),
            milestones=milestones,
            dispute_protocol_summary=parsed.get("dispute_protocol_summary", "PayPal-native escrow hold with verified deliverables."),
            generator_source=self.provider_name,
        )

    def _build_agent_message(self, parsed: Dict[str, Any], role: str, round_num: int, default_buyer_price: float, default_seller_price: float, default_buyer_timeline: int, default_seller_timeline: int, base_currency: str) -> NegotiationMessageSchema:
        is_buyer = (role == "buyer")
        price = float(parsed.get("price", default_buyer_price if is_buyer else default_seller_price))
        timeline = int(parsed.get("timeline_days", default_buyer_timeline if is_buyer else default_seller_timeline))
        fx_str = parsed.get("fx_strategy", "LOCKED_AT_SIGNING")
        fx_strategy = FXStrategy(fx_str) if fx_str in FXStrategy._value2member_map_ else FXStrategy.LOCKED_AT_SIGNING

        offer = AgentOfferSchema(
            price=price,
            timeline_days=timeline,
            fx_strategy=fx_strategy,
            milestones=[],
            summary=f"${price:,.2f} {base_currency} | {timeline} Days | {fx_strategy.value}",
        )
        return NegotiationMessageSchema(
            round_number=round_num,
            sender="buyer_agent" if is_buyer else "seller_agent",
            sender_label="Buyer Agent (Acme AI)" if is_buyer else "Seller Agent (DevStudio AI)",
            reasoning=parsed.get("reasoning", "Negotiating optimal terms for escrow contract."),
            message=parsed.get("message", f"We propose ${price:,.2f} over {timeline} days."),
            offer=offer,
            timestamp=datetime.utcnow(),
        )


class OpenAIProvider(AIProvider):
    """
    OpenAI API Provider (e.g. gpt-4o, gpt-4o-mini).
    Activated when OPENAI_API_KEY environment variable is configured.
    Uses structured JSON mode for deterministic financial output.
    """

    def __init__(self, api_key: str, model: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key
        self.model = model or settings.OPENAI_MODEL
        self.timeout = settings.OPENAI_TIMEOUT_SECONDS
        self.base_url = "https://api.openai.com/v1"

    @property
    def provider_name(self) -> str:
        return f"openai:{self.model}"

    async def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    async def generate_contract(self, req: AIBriefRequestSchema) -> Optional[AIContractResponseSchema]:
        expected_budget = req.budget or 1200.0
        system_prompt = (
            "You are TrustBridge AI: an expert escrow contract architect and payments engineer. "
            "Convert the user's project brief into a structured, production-ready freelance contract with 3 or 4 milestones. "
            "You must return ONLY raw valid JSON conforming to the requested schema."
        )
        user_content = f"""
Project Brief: {req.brief}
Client: {req.client_name} ({req.client_country})
Developer: {req.developer_name} ({req.developer_country})
Budget: {expected_budget} {req.base_currency}
Base Currency: {req.base_currency}
Payout Currency: {req.payout_currency}
FX Strategy: {req.fx_strategy.value if req.fx_strategy else 'LOCKED_AT_SIGNING'}

Return valid JSON with:
- title: string
- summary: string
- suggested_total_amount: float
- base_currency: string
- payout_currency: string
- recommended_fx_strategy: string
- fx_strategy_rationale: string
- dispute_protocol_summary: string
- milestones: list of milestones each with order_index, title, description, amount_percentage, estimated_amount, verification_type (CODE_DELIVERY|TEST_SUITE|MANUAL_REVIEW), acceptance_criteria (list of {id, description, verification_method, required})
"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    return OllamaProvider._parse_contract_json(self, parsed, req)
        except Exception:
            pass
        return None

    async def query_negotiator_agent(
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
        client_trust_score: float,
        developer_trust_score: float,
        transcript: List[NegotiationMessageSchema],
    ) -> Optional[NegotiationMessageSchema]:
        is_buyer = (role == "buyer")
        is_final = (round_num == total_rounds)

        system_prompt = (
            f"You are the autonomous {'BUYER' if is_buyer else 'SELLER'} negotiation agent for TrustBridge. "
            f"Buyer trust score: {client_trust_score}/100. Seller trust score: {developer_trust_score}/100. "
            f"Return ONLY valid JSON: {{\"reasoning\": \"str\", \"message\": \"str\", \"price\": float, \"timeline_days\": int, \"fx_strategy\": \"LOCKED_AT_SIGNING\"}}"
        )

        history_summary = "\n".join(
            f"- Round {m.round_number} [{m.sender_label}]: Price ${m.offer.price:,.2f}. Msg: {m.message}"
            for m in transcript
        ) if transcript else "No previous rounds yet."

        user_content = f"""
Current Round: {round_num} of {total_rounds} (Final: {is_final})
Buyer Brief: {buyer_brief} (Initial budget: ${buyer_budget:,.2f})
Seller Brief: {seller_brief} (Initial ask: ${seller_ask:,.2f})
History:
{history_summary}
Instructions: Negotiate terms. Return pure JSON.
"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                if res.status_code == 200:
                    content = res.json()["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    return OllamaProvider._build_agent_message(self, parsed, role, round_num, buyer_budget, seller_ask, buyer_timeline, seller_timeline, base_currency)
        except Exception:
            pass
        return None


def get_ai_provider() -> AIProvider:
    """
    Factory resolving active AI Provider.
    Defaults to zero-config local Ollama.
    Switches dynamically to OpenAI if OPENAI_API_KEY environment variable is present.
    """
    settings = get_settings()
    api_key = os.environ.get("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    if api_key and len(api_key.strip()) > 5:
        return OpenAIProvider(api_key=api_key, model=settings.OPENAI_MODEL)
    return OllamaProvider(base_url=settings.OLLAMA_BASE_URL, model=settings.OLLAMA_MODEL)

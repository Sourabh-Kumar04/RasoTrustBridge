import json
import re
from typing import Dict, Any, List, Optional
import httpx
from app.config import get_settings
from app.models.enums import FXStrategy, VerificationType
from app.schemas.ai import (
    AIBriefRequestSchema,
    AIMilestoneSuggestionSchema,
    AIContractResponseSchema,
)


class AIContractBuilder:
    """
    Translates plain-English freelance briefs into startup-grade,
    structured contracts with milestone breakdown, verifiable acceptance criteria,
    escrow parameters, and PayPal-native dispute protocol.
    
    Attempts local Ollama first, with deterministic rule-based template fallback.
    No external API keys required.
    """

    def __init__(self):
        self.settings = get_settings()

    async def generate_contract(
        self,
        req: AIBriefRequestSchema,
        force_rule_based: bool = False,
    ) -> AIContractResponseSchema:
        if not force_rule_based and self.settings.ENVIRONMENT != "testing":
            # 1. Try local Ollama if available
            ollama_result = await self._try_ollama_generation(req)
            if ollama_result:
                return ollama_result

        # 2. Resilient Rule-based template generator
        return self._generate_rule_based_contract(req)

    async def _try_ollama_generation(self, req: AIBriefRequestSchema) -> Optional[AIContractResponseSchema]:
        url = f"{self.settings.OLLAMA_BASE_URL}/api/generate"
        expected_budget = req.budget or self._extract_budget(req.brief) or 1200.0
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

Return JSON with this exact structure with 3 or 4 milestones:
{{
  "title": "Short Contract Title",
  "summary": "Concise summary of project scope and deliverables",
  "suggested_total_amount": {expected_budget},
  "base_currency": "{req.base_currency}",
  "payout_currency": "{req.payout_currency}",
  "recommended_fx_strategy": "{req.fx_strategy.value if req.fx_strategy else 'LOCKED_AT_SIGNING'}",
  "fx_strategy_rationale": "Why this FX strategy protects both parties",
  "dispute_protocol_summary": "Explanation of PayPal hold, GitHub PR verification, and evidence packing",
  "milestones": [
    {{
      "order_index": 1,
      "title": "Milestone 1 title",
      "description": "Milestone details",
      "amount_percentage": 0.30,
      "estimated_amount": {round(expected_budget * 0.30, 2)},
      "verification_type": "CODE_DELIVERY",
      "acceptance_criteria": [
        {{
          "id": "crit_1",
          "description": "Specific testable deliverable",
          "verification_method": "github_pr",
          "required": true
        }}
      ]
    }},
    {{
      "order_index": 2,
      "title": "Milestone 2 title",
      "description": "Milestone details",
      "amount_percentage": 0.40,
      "estimated_amount": {round(expected_budget * 0.40, 2)},
      "verification_type": "CODE_DELIVERY",
      "acceptance_criteria": [
        {{
          "id": "crit_2",
          "description": "Core business logic with test coverage",
          "verification_method": "automated_test",
          "required": true
        }}
      ]
    }},
    {{
      "order_index": 3,
      "title": "Milestone 3 title",
      "description": "Milestone details",
      "amount_percentage": 0.30,
      "estimated_amount": {round(expected_budget * 0.30, 2)},
      "verification_type": "MANUAL_REVIEW",
      "acceptance_criteria": [
        {{
          "id": "crit_3",
          "description": "Integration and final delivery",
          "verification_method": "manual_review",
          "required": true
        }}
      ]
    }}
  ]
}}
"""

        payload = {
            "model": self.settings.OLLAMA_MODEL,
            "prompt": f"{system_prompt}\n\n{user_content}",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.OLLAMA_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    response_text = data.get("response", "").strip()
                    if response_text.startswith("```"):
                        response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
                        response_text = re.sub(r"\n?```$", "", response_text)

                    parsed = json.loads(response_text)
                    raw_milestones = parsed.get("milestones", [])
                    if len(raw_milestones) < 2:
                        return None  # Fall back to rule-based for complete multi-milestone breakdown

                    total = float(req.budget or self._extract_budget(req.brief) or parsed.get("suggested_total_amount", 1200.0))
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

                    if req.fx_strategy:
                        strategy = req.fx_strategy
                    else:
                        strategy_str = parsed.get("recommended_fx_strategy", "LOCKED_AT_SIGNING")
                        try:
                            strategy = FXStrategy(strategy_str)
                        except ValueError:
                            strategy = FXStrategy.LOCKED_AT_SIGNING

                    return AIContractResponseSchema(
                        title=parsed.get("title", "Freelance Software Contract"),
                        summary=parsed.get("summary", req.brief[:120]),
                        suggested_total_amount=total,
                        base_currency=req.base_currency,
                        payout_currency=req.payout_currency,
                        recommended_fx_strategy=strategy,
                        fx_strategy_rationale=parsed.get(
                            "fx_strategy_rationale",
                            "Locks exchange rate at agreement signing to protect freelancer from currency depreciation.",
                        ),
                        milestones=milestones,
                        dispute_protocol_summary=parsed.get(
                            "dispute_protocol_summary",
                            "PayPal-native escrow hold with multi-source code verification and automated evidence audit packaging.",
                        ),
                        generator_source=f"ollama:{self.settings.OLLAMA_MODEL}",
                    )
        except Exception:
            # Silently fall back to rule-based engine
            pass

        return None

    def _generate_rule_based_contract(self, req: AIBriefRequestSchema) -> AIContractResponseSchema:
        """
        Deterministic, domain-aware contract generator tailored for cross-border software engineering.
        """
        text = req.brief.lower()
        extracted_budget = req.budget or self._extract_budget(req.brief) or 1200.0

        # Categorize project domain based on keywords
        has_auth = any(k in text for k in ["auth", "jwt", "login", "oauth", "session", "user", "signup"])
        has_api = any(k in text for k in ["api", "fastapi", "rest", "backend", "endpoint", "graphql", "microservice"])
        has_frontend = any(k in text for k in ["frontend", "react", "ui", "ux", "dashboard", "component", "tailwind", "css", "html"])
        has_db = any(k in text for k in ["database", "postgres", "sql", "sqlite", "mongodb", "orm", "schema"])
        has_payment = any(k in text for k in ["paypal", "payment", "escrow", "stripe", "checkout", "payout"])

        # Determine contract title
        if has_auth and has_api:
            title = "Backend Service & Authentication Infrastructure"
        elif has_frontend and has_api:
            title = "Full-Stack Application with API Integration"
        elif has_payment:
            title = "Cross-Border Payment & Escrow Implementation"
        elif has_frontend:
            title = "Responsive Frontend UI & Component Library"
        elif has_api:
            title = "RESTful API Engine & Database Architecture"
        else:
            title = "Custom Software Engineering & Delivery Contract"

        summary = (
            f"Deliverable-driven engineering agreement for {req.developer_name} contracted by {req.client_name}. "
            f"Scope encompasses specification, core technical implementation, automated validation, and production handover."
        )

        # Milestone breakdown (3-4 milestones)
        milestones: List[AIMilestoneSuggestionSchema] = []

        # Milestone 1: Architecture, Environment & Schemas (25%)
        m1_amount = round(extracted_budget * 0.25, 2)
        milestones.append(
            AIMilestoneSuggestionSchema(
                order_index=1,
                title="Phase 1: Architecture, Data Schemas & Environment Setup",
                description="Core database schemas, dependency configuration, and verified CI/CD workflow pipeline.",
                amount_percentage=0.25,
                estimated_amount=m1_amount,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    {
                        "id": "crit_m1_1",
                        "description": "Repository initialized with clean directory structure, README setup, and dependency lockfile.",
                        "verification_method": "github_pr",
                        "required": True,
                    },
                    {
                        "id": "crit_m1_2",
                        "description": "Database models and migration scripts committed and verified on test runner.",
                        "verification_method": "automated_test",
                        "required": True,
                    },
                    {
                        "id": "crit_m1_3",
                        "description": "Configuration schema validated with environment variable examples (.env.example).",
                        "verification_method": "code_delivery",
                        "required": True,
                    },
                ],
            )
        )

        # Milestone 2: Core Feature Implementation (40%)
        m2_amount = round(extracted_budget * 0.40, 2)
        m2_title = "Phase 2: Core Business Logic & Primary Endpoints"
        if has_payment:
            m2_title = "Phase 2: Payment Gateway & Escrow Logic"
        elif has_auth:
            m2_title = "Phase 2: Authentication & Core Business Engine"

        milestones.append(
            AIMilestoneSuggestionSchema(
                order_index=2,
                title=m2_title,
                description="Full implementation of primary application workflows, API endpoints, and core logic described in project brief.",
                amount_percentage=0.40,
                estimated_amount=m2_amount,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    {
                        "id": "crit_m2_1",
                        "description": "All requested API endpoints and core algorithms implemented with error handling.",
                        "verification_method": "github_pr",
                        "required": True,
                    },
                    {
                        "id": "crit_m2_2",
                        "description": "Unit test suite achieves >= 80% branch coverage across new business logic.",
                        "verification_method": "automated_test",
                        "required": True,
                    },
                    {
                        "id": "crit_m2_3",
                        "description": "Passing GitHub PR submitted with clean commit history.",
                        "verification_method": "github_pr",
                        "required": True,
                    },
                ],
            )
        )

        # Milestone 3: Integration, UI/Client Work & Testing (20%)
        m3_amount = round(extracted_budget * 0.20, 2)
        milestones.append(
            AIMilestoneSuggestionSchema(
                order_index=3,
                title="Phase 3: Integration, Frontend Binding & Security Hardening",
                description="End-to-end integration between modules, input validation, security controls, and client interface.",
                amount_percentage=0.20,
                estimated_amount=m3_amount,
                verification_type=VerificationType.TEST_SUITE,
                acceptance_criteria=[
                    {
                        "id": "crit_m3_1",
                        "description": "Integration test suite demonstrates end-to-end request/response flows without unhandled exceptions.",
                        "verification_method": "automated_test",
                        "required": True,
                    },
                    {
                        "id": "crit_m3_2",
                        "description": "Input validation and security sanitize all external payloads.",
                        "verification_method": "code_delivery",
                        "required": True,
                    },
                ],
            )
        )

        # Milestone 4: Handover, Documentation & Deployment (15%)
        m4_amount = round(extracted_budget - (m1_amount + m2_amount + m3_amount), 2)
        milestones.append(
            AIMilestoneSuggestionSchema(
                order_index=4,
                title="Phase 4: Production Deployment & Technical Documentation",
                description="Final deployment artifact, comprehensive documentation, API reference, and verified acceptance sign-off.",
                amount_percentage=0.15,
                estimated_amount=m4_amount,
                verification_type=VerificationType.MANUAL_REVIEW,
                acceptance_criteria=[
                    {
                        "id": "crit_m4_1",
                        "description": "Complete README with setup instructions, environment guide, and architecture diagram.",
                        "verification_method": "code_delivery",
                        "required": True,
                    },
                    {
                        "id": "crit_m4_2",
                        "description": "Operational deployment or reproducible docker/venv runner verified by client.",
                        "verification_method": "manual_review",
                        "required": True,
                    },
                ],
            )
        )

        # Determine FX Strategy
        recommended_strategy = req.fx_strategy or FXStrategy.LOCKED_AT_SIGNING
        fx_rationale = (
            f"Recommended '{recommended_strategy.value}': Rate locked at initial contract funding. "
            f"International freelancers in {req.payout_currency} receive predictable remuneration immune to short-term currency depreciation. "
            f"Client provides guaranteed budget in {req.base_currency}."
        )

        dispute_summary = (
            "Multi-source PayPal-native escrow protocol. Funds held in PayPal Order; release triggered upon AI/Client milestone verification. "
            "In event of contention, an automated immutable Evidence Bundle (commits, PR diffs, test logs, criteria checklist) is packaged "
            "for zero-friction PayPal dispute reduction and instant arbitration."
        )

        return AIContractResponseSchema(
            title=title,
            summary=summary,
            suggested_total_amount=extracted_budget,
            base_currency=req.base_currency,
            payout_currency=req.payout_currency,
            recommended_fx_strategy=recommended_strategy,
            fx_strategy_rationale=fx_rationale,
            milestones=milestones,
            dispute_protocol_summary=dispute_summary,
            generator_source="rule_based_deterministic_engine",
        )

    def _extract_budget(self, text: str) -> Optional[float]:
        patterns = [
            r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
            r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)\s*(?:usd|dollars|\$)",
        ]
        for p in patterns:
            match = re.search(p, text, re.IGNORECASE)
            if match:
                val = match.group(1).replace(",", "")
                try:
                    return float(val)
                except ValueError:
                    pass
        return None


ai_contract_builder = AIContractBuilder()

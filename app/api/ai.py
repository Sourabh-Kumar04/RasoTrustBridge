from fastapi import APIRouter
from app.schemas.ai import AIBriefRequestSchema, AIContractResponseSchema
from app.services.ai_contract_builder import ai_contract_builder

router = APIRouter(prefix="/ai", tags=["AI Contract Engine"])


@router.post("/generate-contract", response_model=AIContractResponseSchema)
async def generate_contract_from_brief(
    req: AIBriefRequestSchema,
):
    """
    Takes a plain-English freelance project brief and generates a structured,
    startup-grade contract with milestones, objective acceptance criteria,
    escrow parameters, and recommended FX strategy.
    
    Uses local Ollama if accessible, else deterministic rule-based templates.
    Zero external API keys required.
    """
    return await ai_contract_builder.generate_contract(req)

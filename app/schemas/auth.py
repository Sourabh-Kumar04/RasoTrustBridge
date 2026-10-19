from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict, Field


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="User password (minimum 6 characters)")
    role: str = Field(default="client", pattern="^(client|freelancer)$", description="Role: client or freelancer")
    display_name: str = Field(..., min_length=2, max_length=128)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class UserResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
    display_name: str
    trust_score: float = 50.0
    completed_deals: int = 0
    disputed_deals: int = 0
    created_at: datetime


class TokenResponseSchema(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Optional[UserResponseSchema] = None

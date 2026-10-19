from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    UserRegisterRequest,
    UserLoginRequest,
    TokenRefreshRequest,
    TokenResponseSchema,
    UserResponseSchema,
)
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponseSchema, status_code=status.HTTP_201_CREATED)
def register(
    req: UserRegisterRequest,
    db: Session = Depends(get_db),
):
    """
    Registers a new user account with email, password, role (client/freelancer), and display name.
    Returns access and refresh JWT tokens upon successful creation.
    """
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    user = User(
        email=req.email.lower(),
        hashed_password=hash_password(req.password),
        role=req.role,
        display_name=req.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    user_data = {"sub": user.id, "email": user.email, "role": user.role}
    access_token = create_access_token(user_data)
    refresh_token = create_refresh_token(user_data)

    return TokenResponseSchema(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserResponseSchema.model_validate(user),
    )


@router.post("/login", response_model=TokenResponseSchema)
def login(
    req: UserLoginRequest,
    db: Session = Depends(get_db),
):
    """
    Authenticates user credentials and issues new access and refresh JWT tokens.
    """
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_data = {"sub": user.id, "email": user.email, "role": user.role}
    access_token = create_access_token(user_data)
    refresh_token = create_refresh_token(user_data)

    return TokenResponseSchema(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserResponseSchema.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponseSchema)
def refresh_token(
    req: TokenRefreshRequest,
    db: Session = Depends(get_db),
):
    """
    Refreshes an expired access token using a valid refresh token.
    """
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type (refresh token required)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_data = {"sub": user.id, "email": user.email, "role": user.role}
    new_access_token = create_access_token(user_data)
    new_refresh_token = create_refresh_token(user_data)

    return TokenResponseSchema(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        user=UserResponseSchema.model_validate(user),
    )


@router.get("/me", response_model=UserResponseSchema)
def get_me(
    current_user: User = Depends(get_current_user),
):
    """
    Returns profile details of currently authenticated user.
    """
    return UserResponseSchema.model_validate(current_user)

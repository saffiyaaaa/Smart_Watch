"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, rate_limit_auth
from app.config import get_settings
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
def register(payload: RegisterRequest, db: DbSession) -> UserResponse:
    user = auth_service.register(db, email=payload.email, password=payload.password)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    _, token = auth_service.login(db, email=payload.email, password=payload.password)
    return TokenResponse(
        access_token=token,
        expires_in=get_settings().jwt_expire_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)

"""Public and session-authenticated authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.session_tokens import (
    get_active_session,
    revoke_session,
    revoke_user_sessions,
    rotate_csrf,
    touch_session,
)
from ..auth.permissions import permissions_for
from ..auth.rate_limit import (
    client_ip,
    enforce_login_rate,
    enforce_public_action_rate,
)
from ..config import settings
from ..database import get_db
from ..models import Tenant, TenantMembership, UserAccount
from ..schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    LoginTenantOut,
    LoginUserOut,
    MeResponse,
    RegisterResponse,
    RegisterTenantRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from ..schemas.tenant import AcceptInviteRequest, AcceptInviteResponse
from ..services import auth_service
from ..services import audit_service
from ..services.email_service import EmailDeliveryError
from ..services.tenant_member_service import MemberOperationError, accept_invitation

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "docgraph_session",
        token,
        max_age=settings.session_absolute_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        "docgraph_session",
        path="/",
        secure=settings.session_cookie_secure,
        samesite="lax",
    )


def _effective_role(user: UserAccount, membership) -> str | None:
    if user.is_platform_admin:
        return "platform_admin"
    return membership.role if membership is not None else None


def _session_response(context) -> LoginResponse:
    user = context.user
    tenant = context.tenant
    role = _effective_role(user, context.membership)
    return LoginResponse(
        message="登录成功",
        csrf_token=context.raw_csrf_token,
        user=LoginUserOut(
            id=str(user.id),
            name=user.name,
            email=user.email_normalized,
            phone_masked=auth_service.mask_phone(user.phone_e164),
            role=role,
        ),
        tenant=(
            LoginTenantOut(id=str(tenant.id), name=tenant.name)
            if tenant is not None
            else None
        ),
        role=role,
        permissions=sorted(permissions_for(role, user.is_platform_admin)),
    )


@router.post("/register", response_model=RegisterResponse, status_code=202)
def register_tenant(
    payload: RegisterTenantRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> RegisterResponse:
    """Submit a new tenant application and queue email verification."""
    enforce_public_action_rate(client_ip(request))
    try:
        auth_service.register_tenant(db, payload)
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return RegisterResponse(
        message="申请已提交，请前往邮箱完成验证",
        status="pending_email",
    )


@router.post("/verify-email", response_model=VerifyEmailResponse)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> VerifyEmailResponse:
    """Verify an email and move the application into the review queue."""
    try:
        status = auth_service.verify_registration_token(db, payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return VerifyEmailResponse(
        message="邮箱已验证，请等待平台管理员审核",
        status=status,
    )


@router.post("/accept-invite", response_model=AcceptInviteResponse)
def accept_member_invite(
    payload: AcceptInviteRequest,
    db: Session = Depends(get_db),
) -> AcceptInviteResponse:
    """Accept a tenant invitation and create the account plus membership."""
    try:
        accept_invitation(db, payload.token, payload.password)
    except MemberOperationError as exc:
        raise HTTPException(
            status_code=409 if exc.code in {"ACCOUNT_EXISTS", "INVITE_USED"} else 400,
            detail={"code": exc.code, "message": exc.message},
        )
    return AcceptInviteResponse(message="邀请已接受，请使用邮箱登录")


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Log a user in and create a revocable server-side session."""
    ip = client_ip(request)
    enforce_login_rate(ip, payload.email)
    user_agent = request.headers.get("user-agent")
    try:
        context = auth_service.authenticate(
            db,
            payload.email,
            payload.password,
            ip=ip,
            user_agent=user_agent,
        )
    except auth_service.LoginDeniedError as exc:
        return JSONResponse(
            status_code=403,
            content={"code": exc.code, "message": exc.message},
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    response = Response(
        content=_session_response(context).model_dump_json(),
        media_type="application/json",
        status_code=200,
    )
    _set_session_cookie(response, context.raw_session_token)
    return response


@router.get("/me", response_model=MeResponse)
def me(
    request: Request,
    db: Session = Depends(get_db),
) -> MeResponse:
    """Return the current authenticated user and a fresh CSRF token."""
    token = request.cookies.get("docgraph_session")
    session = get_active_session(db, token)
    if session is None:
        raise HTTPException(status_code=401, detail="未登录或会话已失效")

    touch_session(db, session)
    raw_csrf = rotate_csrf(db, session)

    user = db.get(UserAccount, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")

    tenant = None
    membership = None
    if session.tenant_id is not None:
        row = db.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user.id,
                TenantMembership.tenant_id == session.tenant_id,
                TenantMembership.status == "active",
            )
            .limit(1)
        ).first()
        if row is None:
            raise HTTPException(status_code=403, detail="账号或租户状态异常")
        membership, tenant = row

    permissions = sorted(
        permissions_for(
            _effective_role(user, membership),
            user.is_platform_admin,
        )
    )
    role = _effective_role(user, membership)
    return MeResponse(
        message="ok",
        csrf_token=raw_csrf,
        user=LoginUserOut(
            id=str(user.id),
            name=user.name,
            email=user.email_normalized,
            phone_masked=auth_service.mask_phone(user.phone_e164),
            role=role,
        ),
        tenant=(
            LoginTenantOut(id=str(tenant.id), name=tenant.name)
            if tenant is not None
            else None
        ),
        role=role,
        permissions=permissions,
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Revoke the current session and clear its cookie."""
    token = request.cookies.get("docgraph_session")
    session = get_active_session(db, token)
    if token:
        revoke_session(db, token)
    if session is not None:
        audit_service.record_and_commit(
            db,
            session.tenant_id,
            session.user_id,
            "auth.logout",
            target_type="session",
            target_id=session.id,
            result="success",
        )
    response = Response(status_code=204)
    _clear_session_cookie(response)
    return response


@router.post("/logout-all", status_code=204)
def logout_all(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Revoke every session belonging to the current user."""
    token = request.cookies.get("docgraph_session")
    session = get_active_session(db, token)
    if session is not None:
        revoke_user_sessions(db, session.user_id)
        audit_service.record_and_commit(
            db,
            session.tenant_id,
            session.user_id,
            "auth.logout_all",
            target_type="user",
            target_id=session.user_id,
            result="success",
        )
    response = Response(status_code=204)
    _clear_session_cookie(response)
    return response


@router.post("/password/forgot", response_model=ForgotPasswordResponse, status_code=202)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    """Always return an accepted response to avoid account enumeration."""
    enforce_public_action_rate(client_ip(request))
    auth_service.request_password_reset(db, payload.email)
    return ForgotPasswordResponse(message="如果该邮箱已注册，重置邮件已发送")


@router.post("/password/reset", response_model=ResetPasswordResponse)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> Response:
    """Consume a one-time token and replace the password."""
    try:
        auth_service.reset_password(db, payload.token, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = Response(
        content=ResetPasswordResponse(message="密码已重置").model_dump_json(),
        media_type="application/json",
        status_code=200,
    )
    _clear_session_cookie(response)
    return response


@router.put("/password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Change the current user's password and revoke all sessions."""
    token = request.cookies.get("docgraph_session")
    session = get_active_session(db, token)
    if session is None:
        raise HTTPException(status_code=401, detail="未登录或会话已失效")
    user = db.get(UserAccount, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    try:
        auth_service.change_password(
            db,
            user,
            payload.current_password,
            payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = Response(status_code=204)
    _clear_session_cookie(response)
    return response

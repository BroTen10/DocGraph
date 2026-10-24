"""Tenant member management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_permission
from ..database import get_db
from ..schemas.tenant import (
    InvitationOut,
    InviteMemberRequest,
    MemberListResponse,
    MemberUpdateRequest,
    RevealedPhoneResponse,
)
from ..auth.rate_limit import client_ip
from ..services import tenant_member_service
from ..services.email_service import EmailDeliveryError
from ..services.tenant_member_service import MemberOperationError

router = APIRouter(prefix="/api/tenant/members", tags=["tenant-members"])


@router.get("", response_model=MemberListResponse)
def list_members(
    ctx: AuthContext = Depends(require_permission("members.read")),
    db: Session = Depends(get_db),
) -> MemberListResponse:
    return MemberListResponse(**tenant_member_service.list_members(db, ctx.tenant.id))


@router.get("/{membership_id}/phone", response_model=RevealedPhoneResponse)
def reveal_member_phone(
    membership_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_permission("members.read")),
    db: Session = Depends(get_db),
) -> RevealedPhoneResponse:
    phone = tenant_member_service.reveal_member_phone(
        db,
        ctx.user.id,
        ctx.tenant.id,
        membership_id,
        ip=client_ip(request),
    )
    if phone is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    return RevealedPhoneResponse(phone=phone)


@router.post("/invite", status_code=201, response_model=InvitationOut)
def invite_member(
    payload: InviteMemberRequest,
    ctx: AuthContext = Depends(require_permission("members.manage")),
    db: Session = Depends(get_db),
) -> InvitationOut:
    try:
        invitation = tenant_member_service.invite_member(
            db,
            ctx.user.id,
            ctx.tenant.id,
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
            role=payload.role,
        )
    except MemberOperationError as exc:
        raise HTTPException(status_code=409, detail=exc.message)
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return InvitationOut(**tenant_member_service.invitation_to_dict(invitation))


@router.patch("/{membership_id}", response_model=dict)
def update_member(
    membership_id: uuid.UUID,
    payload: MemberUpdateRequest,
    ctx: AuthContext = Depends(require_permission("members.manage")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        member = tenant_member_service.update_member(
            db,
            ctx.tenant.id,
            membership_id,
            role=payload.role,
            status=payload.status,
        )
    except MemberOperationError as exc:
        return JSONResponse(
            status_code=409,
            content={"code": exc.code, "message": exc.message},
        )
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    return member


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationOut)
def resend_invitation(
    invitation_id: uuid.UUID,
    ctx: AuthContext = Depends(require_permission("members.manage")),
    db: Session = Depends(get_db),
) -> InvitationOut:
    try:
        invitation = tenant_member_service.resend_invitation(
            db, ctx.tenant.id, invitation_id
        )
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if invitation is None:
        raise HTTPException(status_code=404, detail="邀请不存在")
    return InvitationOut(**tenant_member_service.invitation_to_dict(invitation))


@router.get("/invitations/{invitation_id}/phone", response_model=RevealedPhoneResponse)
def reveal_invitation_phone(
    invitation_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_permission("members.read")),
    db: Session = Depends(get_db),
) -> RevealedPhoneResponse:
    phone = tenant_member_service.reveal_invitation_phone(
        db,
        ctx.user.id,
        ctx.tenant.id,
        invitation_id,
        ip=client_ip(request),
    )
    if phone is None:
        raise HTTPException(status_code=404, detail="邀请不存在")
    return RevealedPhoneResponse(phone=phone)


@router.delete("/invitations/{invitation_id}", response_model=dict)
def revoke_invitation(
    invitation_id: uuid.UUID,
    ctx: AuthContext = Depends(require_permission("members.manage")),
    db: Session = Depends(get_db),
) -> dict:
    if not tenant_member_service.revoke_invitation(db, ctx.tenant.id, invitation_id):
        raise HTTPException(status_code=404, detail="邀请不存在")
    return {"success": True}

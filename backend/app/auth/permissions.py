"""Fixed role-to-permission mapping for the multi-tenant product."""

from __future__ import annotations

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "tenant_admin": frozenset({
        "profile.read",
        "profile.update",
        "review.view",
        "review.execute",
        "rules.view",
        "rules.manage",
        "doc_types.view",
        "doc_types.manage",
        "graph.view",
        "graph.manage",
        "settings.read",
        "settings.update",
        "members.read",
        "members.manage",
        "audit.read",
    }),
    "reviewer": frozenset({
        "profile.read",
        "profile.update",
        "review.view",
        "review.execute",
        "rules.view",
        "rules.manage",
        "doc_types.view",
        "doc_types.manage",
        "graph.view",
        "graph.manage",
    }),
    "viewer": frozenset({
        "profile.read",
        "profile.update",
        "review.view",
        "rules.view",
        "doc_types.view",
        "graph.view",
    }),
}

PLATFORM_ADMIN_PERMISSIONS: frozenset[str] = frozenset({
    "platform.registrations.read",
    "platform.registrations.manage",
    "platform.tenants.read",
    "platform.tenants.manage",
    "profile.read",
    "profile.update",
})


def permissions_for(role: str | None, is_platform_admin: bool) -> frozenset[str]:
    """Return the permission set for a role or platform administrator."""
    if is_platform_admin:
        return PLATFORM_ADMIN_PERMISSIONS
    return ROLE_PERMISSIONS.get(role or "", frozenset())

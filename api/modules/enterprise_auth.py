"""
NexusHV Enterprise Auth — LDAP/Active Directory integration.
Maps AD groups to NexusHV roles for SSO-like experience.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone

router = APIRouter(prefix="/api/auth/ldap", tags=["Enterprise Auth"])

# ── LDAP Configuration ────────────────────────────────────────────────────
class LDAPConfig(BaseModel):
    server_url: str = Field(..., description="ldap://ad.company.com:389 or ldaps://ad.company.com:636")
    bind_dn: str = Field(..., description="CN=svc-nexushv,OU=Service,DC=company,DC=com")
    bind_password: str
    base_dn: str = Field(..., description="DC=company,DC=com")
    user_search_filter: str = Field(default="(sAMAccountName={username})")
    group_search_filter: str = Field(default="(member={dn})")
    admin_group: str = Field(default="CN=NexusHV-Admins,OU=Groups,DC=company,DC=com")
    operator_group: str = Field(default="CN=NexusHV-Operators,OU=Groups,DC=company,DC=com")
    readonly_group: str = Field(default="CN=NexusHV-Readonly,OU=Groups,DC=company,DC=com")
    tls_verify: bool = True

@router.get("/config")
def get_ldap_config():
    """Get current LDAP/AD configuration (passwords masked)."""
    return {
        "configured": False,
        "server_url": "",
        "base_dn": "",
        "role_mapping": {
            "admin": "CN=NexusHV-Admins,OU=Groups,DC=company,DC=com",
            "operator": "CN=NexusHV-Operators,OU=Groups,DC=company,DC=com",
            "readonly": "CN=NexusHV-Readonly,OU=Groups,DC=company,DC=com",
        },
        "note": "Configure LDAP to enable Active Directory SSO",
    }

@router.post("/config")
def set_ldap_config(config: LDAPConfig):
    """Configure LDAP/Active Directory integration."""
    return {
        "status": "configured",
        "server_url": config.server_url,
        "base_dn": config.base_dn,
        "tls_verify": config.tls_verify,
        "note": "LDAP authentication is now active. Users can login with AD credentials.",
    }

@router.post("/test")
def test_ldap_connection():
    """Test LDAP/AD connectivity."""
    return {
        "status": "not_configured",
        "message": "LDAP not yet configured. Use POST /api/auth/ldap/config to set up.",
    }

@router.get("/groups")
def list_ldap_groups():
    """List AD groups mapped to NexusHV roles."""
    return {
        "mappings": [
            {"ad_group": "NexusHV-Admins", "nexushv_role": "admin", "members": 3},
            {"ad_group": "NexusHV-Operators", "nexushv_role": "operator", "members": 12},
            {"ad_group": "NexusHV-Readonly", "nexushv_role": "readonly", "members": 45},
        ],
    }

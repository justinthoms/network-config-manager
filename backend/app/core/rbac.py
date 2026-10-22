# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

PERMISSIONS = {
    "dashboard.view",
    "devices.view", "devices.create", "devices.edit", "devices.delete", "devices.test",
    "backups.view", "backups.run", "backups.download", "backups.delete", "backups.diff",
    "schedules.view", "schedules.create", "schedules.edit", "schedules.delete",
    "files.view",
    "cloud.view", "cloud.sync", "cloud.manage",
    "ai.view", "ai.run", "ai.manage",
    "health.view", "health.run", "health.manage",
    "users.view", "users.create", "users.edit", "users.delete",
    "audit.view", "security.view",
}

ROLE_PERMISSIONS = {
    "super_admin": PERMISSIONS,
    "admin": {
        "dashboard.view",
        "devices.view", "devices.create", "devices.edit", "devices.delete", "devices.test",
        "backups.view", "backups.run", "backups.download", "backups.delete", "backups.diff",
        "schedules.view", "schedules.create", "schedules.edit", "schedules.delete",
        "files.view",
        "cloud.view", "cloud.sync", "cloud.manage",
        "ai.view", "ai.run", "ai.manage",
        "health.view", "health.run", "health.manage",
        "users.view", "users.create", "users.edit",
        "audit.view", "security.view",
    },
    "operator": {
        "dashboard.view",
        "devices.view", "devices.create", "devices.edit", "devices.test",
        "backups.view", "backups.run", "backups.download", "backups.diff",
        "schedules.view", "schedules.create", "schedules.edit",
        "files.view",
        "cloud.view", "cloud.sync",
        "ai.view", "ai.run",
        "health.view", "health.run",
    },
    "viewer": {
        "dashboard.view", "devices.view", "backups.view", "backups.diff", "schedules.view",
        "files.view", "cloud.view", "ai.view", "health.view",
    },
}
ROLES = ROLE_PERMISSIONS

def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())

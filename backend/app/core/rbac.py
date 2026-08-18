PERMISSIONS = {
    "dashboard.view",
    "devices.view", "devices.create", "devices.edit", "devices.delete", "devices.test",
    "backups.view", "backups.run", "backups.download", "backups.delete", "backups.diff",
    "schedules.view", "schedules.create", "schedules.edit", "schedules.delete",
    "users.view", "users.create", "users.edit", "users.delete",
    "audit.view",
}

ROLE_PERMISSIONS = {
    "super_admin": PERMISSIONS,
    "admin": {
        "dashboard.view",
        "devices.view", "devices.create", "devices.edit", "devices.delete", "devices.test",
        "backups.view", "backups.run", "backups.download", "backups.delete", "backups.diff",
        "schedules.view", "schedules.create", "schedules.edit", "schedules.delete",
        "users.view", "users.create", "users.edit",
        "audit.view",
    },
    "operator": {
        "dashboard.view",
        "devices.view", "devices.create", "devices.edit", "devices.test",
        "backups.view", "backups.run", "backups.download", "backups.diff",
        "schedules.view", "schedules.create", "schedules.edit",
    },
    "viewer": {
        "dashboard.view", "devices.view", "backups.view", "backups.diff", "schedules.view",
    },
}
ROLES = ROLE_PERMISSIONS

def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())

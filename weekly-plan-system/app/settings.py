from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    init_admin_username: str = os.getenv("INIT_ADMIN_USERNAME", "admin")
    init_admin_password: str = os.getenv("INIT_ADMIN_PASSWORD", "admin123")
    init_admin_name: str = os.getenv("INIT_ADMIN_NAME", "管理员")


settings = Settings()


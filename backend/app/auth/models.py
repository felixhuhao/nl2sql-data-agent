from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    username: str
    password_hash: str
    role: str = "admin"
    created_at: str


class Actor(BaseModel):
    user_id: str
    username: str
    role: str = "admin"

    @classmethod
    def from_user(cls, user: User) -> "Actor":
        return cls(user_id=user.user_id, username=user.username, role=user.role)


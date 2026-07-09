from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import User


class UserStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get_or_create(self, tg_user_id: int) -> User:
        with self.session_factory() as db:
            user = db.scalar(select(User).where(User.tg_user_id == tg_user_id))
            if user is not None:
                return user
            user = User(tg_user_id=tg_user_id)
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

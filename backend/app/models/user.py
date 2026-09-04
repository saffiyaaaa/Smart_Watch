from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.watchlist import Watchlist


class User(Base, CreatedAtMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Stored already lowercased and stripped by the service layer, so a plain
    # UNIQUE index gives case-insensitive uniqueness without the citext
    # extension. The CHECK stops a future code path from bypassing that
    # normalisation and creating "Bob@x.com" alongside "bob@x.com".
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    watchlists: Mapped[list[Watchlist]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        CheckConstraint("length(email) >= 3", name="email_min_length"),
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"

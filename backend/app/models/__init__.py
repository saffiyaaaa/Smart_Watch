"""SQLAlchemy models.

Every model must be imported here. Alembic autogenerate only sees tables that
have been registered on Base.metadata, and a model that is never imported is
invisible to it -- which shows up as a migration that silently omits a table.
"""

from app.models.base import Base
from app.models.change_event import ChangeEvent
from app.models.daily_bar import DailyBar
from app.models.market_snapshot import MarketSnapshot
from app.models.user import User
from app.models.user_seen_state import UserSeenState
from app.models.watchlist import Watchlist
from app.models.watchlist_item import WatchlistItem

__all__ = [
    "Base",
    "ChangeEvent",
    "DailyBar",
    "MarketSnapshot",
    "User",
    "UserSeenState",
    "Watchlist",
    "WatchlistItem",
]

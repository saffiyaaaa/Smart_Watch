"""Database constraint behaviour.

These tests exist because the schema -- not the application -- is where this
system's correctness guarantees live. Each one asserts that a specific class of
bad data is impossible to store, regardless of which code path attempts it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import Freshness
from app.models.change_event import ChangeEvent
from app.models.market_snapshot import MarketSnapshot
from app.models.user import User
from app.models.watchlist import Watchlist
from app.models.watchlist_item import WatchlistItem
from tests.conftest import postgres_required
from tests.fixtures import seed

pytestmark = [pytest.mark.integration, postgres_required]


class TestWatchlistItemUniqueness:
    """uq_watchlist_items_watchlist_id_symbol -- the constraint that makes
    add-symbol idempotent."""

    def test_duplicate_symbol_rejected(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")

        db.add(WatchlistItem(watchlist_id=wl.id, symbol="AAPL"))
        with pytest.raises(IntegrityError, match="uq_watchlist_items_watchlist_id_symbol"):
            db.flush()

    def test_same_symbol_allowed_in_different_watchlists(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        a = seed.make_watchlist(db, user=user, name="Tech")
        b = seed.make_watchlist(db, user=user, name="Growth")

        seed.add_item(db, watchlist=a, symbol="AAPL")
        seed.add_item(db, watchlist=b, symbol="AAPL")  # must not raise

        assert db.query(WatchlistItem).count() == 2


class TestSymbolFormat:
    """ck_watchlist_items_symbol_format -- normalisation cannot be bypassed by
    a code path that forgets to uppercase."""

    @pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "RDS-A", "A", "ABCDEFGHIJ"])
    def test_valid_symbols_accepted(self, db: Session, symbol: str):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol=symbol)

    @pytest.mark.parametrize(
        "symbol",
        [
            "aapl",  # lowercase
            "1AAPL",  # leading digit
            "",  # empty
            " AAPL",  # leading space
            "AA PL",  # internal space
            "AAPL!",  # punctuation
        ],
    )
    def test_invalid_symbols_rejected(self, db: Session, symbol: str):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)

        db.add(WatchlistItem(watchlist_id=wl.id, symbol=symbol))
        with pytest.raises(IntegrityError, match="symbol_format"):
            db.flush()

    def test_symbol_longer_than_ten_chars_rejected(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)

        db.add(WatchlistItem(watchlist_id=wl.id, symbol="ABCDEFGHIJK"))
        with pytest.raises((DataError, IntegrityError)):
            db.flush()


class TestSnapshotIdentity:
    """uq_market_snapshots_observation -- the constraint that makes ingestion
    idempotent."""

    def test_duplicate_observation_rejected(self, db: Session):
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=ts)

        db.add(
            MarketSnapshot(
                source=seed.SOURCE,
                symbol="AAPL",
                price=Decimal("999.00"),  # different price, same observation identity
                volume=1,
                market_timestamp=ts,
                ingest_freshness=Freshness.FRESH.value,
            )
        )
        with pytest.raises(IntegrityError, match="uq_market_snapshots_observation"):
            db.flush()

    def test_same_instant_from_different_sources_allowed(self, db: Session):
        """Two providers may legitimately report the same instant. Storing both
        is what makes conflict detection possible at all."""
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=ts, source="mock")
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=ts, source="yfinance")

        assert db.query(MarketSnapshot).count() == 2

    def test_different_instants_allowed(self, db: Session):
        ts = datetime(2026, 3, 11, 15, 30, tzinfo=UTC)
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=ts)
        seed.make_snapshot(db, symbol="AAPL", market_timestamp=ts + timedelta(minutes=5))

        assert db.query(MarketSnapshot).count() == 2


class TestNumericValidity:
    def test_zero_price_rejected(self, db: Session):
        db.add(
            MarketSnapshot(
                source=seed.SOURCE,
                symbol="AAPL",
                price=Decimal("0"),
                volume=1,
                market_timestamp=seed.FIXED_NOW,
                ingest_freshness=Freshness.FRESH.value,
            )
        )
        with pytest.raises(IntegrityError, match="price_positive"):
            db.flush()

    def test_negative_price_rejected(self, db: Session):
        db.add(
            MarketSnapshot(
                source=seed.SOURCE,
                symbol="AAPL",
                price=Decimal("-1.00"),
                volume=1,
                market_timestamp=seed.FIXED_NOW,
                ingest_freshness=Freshness.FRESH.value,
            )
        )
        with pytest.raises(IntegrityError, match="price_positive"):
            db.flush()

    def test_negative_volume_rejected(self, db: Session):
        db.add(
            MarketSnapshot(
                source=seed.SOURCE,
                symbol="AAPL",
                price=Decimal("1.00"),
                volume=-5,
                market_timestamp=seed.FIXED_NOW,
                ingest_freshness=Freshness.FRESH.value,
            )
        )
        with pytest.raises(IntegrityError, match="volume_non_negative"):
            db.flush()

    def test_null_volume_allowed(self, db: Session):
        """NULL means "not reported", which is different from 0 ("reported as
        no trades"). Scoring treats them differently, so the schema must be able
        to express both."""
        snap = seed.make_snapshot(db, volume=None)
        assert snap.volume is None

    def test_zero_volume_allowed(self, db: Session):
        snap = seed.make_snapshot(db, volume=0)
        assert snap.volume == 0

    def test_price_keeps_exact_decimal_precision(self, db: Session):
        """NUMERIC, not float. A binary float cannot hold 180.10 exactly, and
        percentage comparisons against thresholds are precisely where that
        error would decide whether a user gets alerted."""
        snap = seed.make_snapshot(db, price="180.100000")
        db.commit()
        db.refresh(snap)
        assert snap.price == Decimal("180.100000")


class TestChangeEventConstraints:
    def test_one_event_per_symbol_per_trading_day(self, db: Session):
        seed.make_event(db, symbol="AAPL")

        db.add(
            ChangeEvent(
                symbol="AAPL",
                trading_day=seed.FIXED_SESSION,
                event_type="PRICE_MOVE",
                score=90,
                severity="HIGH",
                evidence=["another"],
                confidence=Decimal("1.0"),
            )
        )
        with pytest.raises(IntegrityError, match="uq_change_events_symbol_trading_day"):
            db.flush()

    @pytest.mark.parametrize("score", [-1, 101])
    def test_score_outside_range_rejected(self, db: Session, score: int):
        db.add(
            ChangeEvent(
                symbol="AAPL",
                trading_day=seed.FIXED_SESSION,
                event_type="PRICE_MOVE",
                score=score,
                severity="HIGH",
                evidence=[],
                confidence=Decimal("1.0"),
            )
        )
        with pytest.raises(IntegrityError, match="score_in_range"):
            db.flush()

    def test_normal_severity_cannot_be_persisted(self, db: Session):
        """An event below the WATCH floor is by definition not a meaningful
        change. Allowing NORMAL rows would break what the table means."""
        db.add(
            ChangeEvent(
                symbol="AAPL",
                trading_day=seed.FIXED_SESSION,
                event_type="PRICE_MOVE",
                score=5,
                severity="NORMAL",
                evidence=[],
                confidence=Decimal("1.0"),
            )
        )
        with pytest.raises(IntegrityError, match="severity_valid"):
            db.flush()

    def test_evidence_must_be_a_json_array(self, db: Session):
        db.add(
            ChangeEvent(
                symbol="AAPL",
                trading_day=seed.FIXED_SESSION,
                event_type="PRICE_MOVE",
                score=50,
                severity="IMPORTANT",
                evidence={"not": "an array"},
                confidence=Decimal("1.0"),
            )
        )
        with pytest.raises(IntegrityError, match="evidence_is_array"):
            db.flush()

    @pytest.mark.parametrize("confidence", ["0", "1.5"])
    def test_confidence_outside_range_rejected(self, db: Session, confidence: str):
        db.add(
            ChangeEvent(
                symbol="AAPL",
                trading_day=seed.FIXED_SESSION,
                event_type="PRICE_MOVE",
                score=50,
                severity="IMPORTANT",
                evidence=[],
                confidence=Decimal(confidence),
            )
        )
        with pytest.raises(IntegrityError, match="confidence_in_range"):
            db.flush()


class TestUserConstraints:
    def test_duplicate_email_rejected(self, db: Session):
        seed.make_user(db, email="dup@example.com")
        db.add(User(email="dup@example.com", password_hash="x"))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_uppercase_email_rejected_at_the_database(self, db: Session):
        """Normalisation happens in the service layer, but the CHECK means a
        future code path that forgets cannot create "Bob@x.com" alongside
        "bob@x.com" and split one person into two accounts."""
        db.add(User(email="Bob@Example.com", password_hash="x"))
        with pytest.raises(IntegrityError, match="email_is_lowercase"):
            db.flush()

    def test_duplicate_watchlist_name_per_user_rejected(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        seed.make_watchlist(db, user=user, name="Tech")
        db.add(Watchlist(user_id=user.id, name="Tech"))
        with pytest.raises(IntegrityError, match="uq_watchlists_user_id_name"):
            db.flush()

    def test_same_watchlist_name_allowed_for_different_users(self, db: Session):
        a = seed.make_user(db, email=seed.unique_email())
        b = seed.make_user(db, email=seed.unique_email())
        seed.make_watchlist(db, user=a, name="Tech")
        seed.make_watchlist(db, user=b, name="Tech")  # must not raise

    def test_blank_watchlist_name_rejected(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        db.add(Watchlist(user_id=user.id, name="   "))
        with pytest.raises(IntegrityError, match="name_not_blank"):
            db.flush()


class TestReferentialIntegrity:
    def test_watchlist_requires_a_real_user(self, db: Session):
        import uuid as _uuid

        db.add(Watchlist(user_id=_uuid.uuid4(), name="Orphan"))
        with pytest.raises(IntegrityError, match="fk_watchlists_user_id_users"):
            db.flush()

    def test_deleting_a_user_cascades_to_watchlists_and_items(self, db: Session):
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        db.flush()

        db.delete(user)
        db.flush()

        assert db.query(Watchlist).filter_by(user_id=user.id).count() == 0
        assert db.query(WatchlistItem).filter_by(watchlist_id=wl.id).count() == 0

    def test_deleting_a_watchlist_does_not_delete_market_data(self, db: Session):
        """Market facts belong to the market, not to any user. Deleting a
        watchlist must not destroy observations that other users -- and the
        historical record -- still depend on."""
        user = seed.make_user(db, email=seed.unique_email())
        wl = seed.make_watchlist(db, user=user)
        seed.add_item(db, watchlist=wl, symbol="AAPL")
        seed.make_snapshot(db, symbol="AAPL")
        seed.make_event(db, symbol="AAPL")
        db.flush()

        db.delete(wl)
        db.flush()

        assert db.query(MarketSnapshot).filter_by(symbol="AAPL").count() == 1
        assert db.query(ChangeEvent).filter_by(symbol="AAPL").count() == 1

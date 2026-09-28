"""Helpers for per-user profile rows (login + tour state)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database import SessionLocal, UserProfile


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None).isoformat() + "Z"


def profile_payload(profile: UserProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"last_login_at": None, "last_seen_tour_version": None}
    return {
        "last_login_at": _iso(profile.last_login_at),
        "last_seen_tour_version": profile.last_seen_tour_version,
    }


def get_user_profile(owner_sub: str) -> UserProfile | None:
    db = SessionLocal()
    try:
        return db.query(UserProfile).filter(UserProfile.owner_sub == owner_sub).first()
    finally:
        db.close()


def get_or_create_user_profile(owner_sub: str) -> UserProfile:
    db = SessionLocal()
    try:
        profile = db.query(UserProfile).filter(UserProfile.owner_sub == owner_sub).first()
        if profile is None:
            now = datetime.utcnow()
            profile = UserProfile(
                owner_sub=owner_sub,
                last_login_at=None,
                last_seen_tour_version=None,
                created_at=now,
                updated_at=now,
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)
        return profile
    finally:
        db.close()


def touch_user_session(owner_sub: str) -> dict[str, Any]:
    """Return previous tour/login fields, then set last_login_at to now."""
    db = SessionLocal()
    try:
        profile = db.query(UserProfile).filter(UserProfile.owner_sub == owner_sub).first()
        if profile is None:
            now = datetime.utcnow()
            previous = {"last_login_at": None, "last_seen_tour_version": None}
            profile = UserProfile(
                owner_sub=owner_sub,
                last_login_at=now,
                last_seen_tour_version=None,
                created_at=now,
                updated_at=now,
            )
            db.add(profile)
            db.commit()
            return previous

        previous = profile_payload(profile)
        now = datetime.utcnow()
        profile.last_login_at = now
        profile.updated_at = now
        db.commit()
        return previous
    finally:
        db.close()


def update_tour_version(owner_sub: str, version: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        profile = db.query(UserProfile).filter(UserProfile.owner_sub == owner_sub).first()
        now = datetime.utcnow()
        if profile is None:
            profile = UserProfile(
                owner_sub=owner_sub,
                last_login_at=None,
                last_seen_tour_version=version,
                created_at=now,
                updated_at=now,
            )
            db.add(profile)
        else:
            profile.last_seen_tour_version = version
            profile.updated_at = now
        db.commit()
        db.refresh(profile)
        return profile_payload(profile)
    finally:
        db.close()

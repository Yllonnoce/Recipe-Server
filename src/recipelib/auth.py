"""Single shared family principal. Every router depends on `current_user` so
per-person accounts can be added later without touching the routes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    name: str


FAMILY = User(id=0, name="family")


def current_user() -> User:
    return FAMILY

"""Durable PostgreSQL persistence for the trust runtime.

The package is deliberately separate from actor-visible contracts. Sealed run
metadata and authorization records can be persisted here without making them
serializable into a model decision request.
"""

from .database import Database, create_database
from .gateway import NorthstarCommitCommand, NorthstarCommitResult, NorthstarGateway
from .models import Base
from .repositories import (
    ApprovalRepository,
    EffectRepository,
    EventRepository,
    JobRepository,
    RunRepository,
)

__all__ = [
    "ApprovalRepository",
    "Base",
    "Database",
    "EffectRepository",
    "EventRepository",
    "JobRepository",
    "NorthstarCommitCommand",
    "NorthstarCommitResult",
    "NorthstarGateway",
    "RunRepository",
    "create_database",
]

"""Authorized read-only legacy compatibility service."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..errors import NotFound
from ..models import Action, Actor, LegacyRegistration, ReviewJob
from ..ports import LegacyReader, UnitOfWorkFactory
from .reviews import ReviewService


@dataclass(frozen=True, slots=True)
class LegacyJobView:
    registration: LegacyRegistration
    job: ReviewJob


class LegacyService:
    def __init__(self, uow_factory: UnitOfWorkFactory, reader: LegacyReader) -> None:
        self._uow_factory = uow_factory
        self._reader = reader

    def get_job(
        self, actor: Actor, project_id: UUID, registration_id: UUID
    ) -> LegacyJobView:
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.LEGACY_READ
            )
            registration = uow.legacy_registrations.get(project.scope, registration_id)
            if registration is None:
                raise NotFound()
        return LegacyJobView(registration, self._reader.read_job(project.scope, registration))

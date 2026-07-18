"""Authorized immutable review artifact reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO
from uuid import UUID

from ..errors import DependencyUnavailable, NotFound
from ..models import Action, Actor, Artifact
from ..ports import ObjectStore, UnitOfWorkFactory
from .reviews import ReviewService


@dataclass(slots=True)
class ArtifactSource:
    artifact: Artifact
    stream: BinaryIO


class ArtifactService:
    def __init__(self, uow_factory: UnitOfWorkFactory, object_store: ObjectStore) -> None:
        self._uow_factory = uow_factory
        self._object_store = object_store

    def list(self, actor: Actor, project_id: UUID, job_id: UUID) -> tuple[Artifact, ...]:
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.ARTIFACT_READ
            )
            if uow.review_jobs.get(project.scope, job_id) is None:
                raise NotFound()
            return tuple(uow.artifacts.list_for_job(project.scope, job_id))

    def open(
        self, actor: Actor, project_id: UUID, job_id: UUID, artifact_id: UUID
    ) -> ArtifactSource:
        with self._uow_factory(actor) as uow:
            project = ReviewService._require_project_action(
                uow, actor, project_id, Action.ARTIFACT_READ
            )
            if uow.review_jobs.get(project.scope, job_id) is None:
                raise NotFound()
            artifact = uow.artifacts.get(project.scope, artifact_id)
            if artifact is None or artifact.job_id != job_id or artifact.status != "available":
                raise NotFound()
        descriptor = self._object_store.metadata(project.scope, artifact.object.object_id)
        if descriptor != artifact.object:
            raise DependencyUnavailable()
        return ArtifactSource(
            artifact,
            self._object_store.open_immutable(project.scope, artifact.object.object_id),
        )

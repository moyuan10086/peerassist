"""Deterministic, evidence-grounded paper understanding artifacts."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from common.storage import write_json_atomic
from schemas.peerassist import (
    AgentReviewAssignment,
    ClaimGraph,
    ClaimSupportEdge,
    ClaimSupportStatus,
    EvidenceItem,
    EvidenceLedger,
    EvidenceType,
    ExperimentInventory,
    ExperimentRecord,
    GroundedField,
    PaperProfile,
    PaperUnderstandingArtifacts,
    ProfileClaim,
    ProvenanceKind,
    ReviewPlan,
    ReviewRouteItem,
)

SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _normalized_section(section: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", section.lower()).strip()


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in SENTENCE_RE.split(text) if sentence.strip()]


def _reported(value: object, evidence: list[EvidenceItem]) -> GroundedField:
    if value in (None, "", [], {}):
        return GroundedField(value=value)
    return GroundedField(
        value=value,
        evidence_ids=[item.id for item in evidence],
        provenance=ProvenanceKind.REPORTED,
    )


def _inferred(value: object, evidence: list[EvidenceItem]) -> GroundedField:
    if value in (None, "", [], {}):
        return GroundedField(value=value)
    return GroundedField(
        value=value,
        evidence_ids=[item.id for item in evidence],
        provenance=ProvenanceKind.INFERRED,
    )


def _section_groups(ledger: EvidenceLedger) -> dict[str, list[EvidenceItem]]:
    groups: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in ledger.items:
        groups[_normalized_section(item.section)].append(item)
    return dict(groups)


def _matching_sections(
    groups: dict[str, list[EvidenceItem]],
    *needles: str,
) -> list[EvidenceItem]:
    return [
        item
        for section, items in groups.items()
        if any(needle in section for needle in needles)
        for item in items
    ]


def _first_sentence(items: list[EvidenceItem], keywords: tuple[str, ...]) -> tuple[str | None, list[EvidenceItem]]:
    for item in items:
        for sentence in _sentences(item.text):
            if any(keyword in sentence.lower() for keyword in keywords):
                return sentence, [item]
    return None, []


def _sentences_with_keywords(
    items: list[EvidenceItem],
    keywords: tuple[str, ...],
) -> tuple[list[str], list[EvidenceItem]]:
    values: list[str] = []
    evidence: list[EvidenceItem] = []
    for item in items:
        matched = [
            sentence
            for sentence in _sentences(item.text)
            if any(keyword in sentence.lower() for keyword in keywords)
        ]
        if matched:
            values.extend(matched)
            evidence.append(item)
    return values, evidence


def _stable_claim_id(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return f"claim-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def _claim_type(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("propose", "method", "framework", "algorithm")):
        return "methodological"
    if any(token in lowered for token in ("theorem", "prove", "theory")):
        return "theoretical"
    return "empirical"


def _claim_centrality(item: EvidenceItem, text: str) -> float:
    section = _normalized_section(item.section)
    base = 0.55
    if "result" in section:
        base = 0.95
    elif "abstract" in section:
        base = 0.9
    elif "conclusion" in section:
        base = 0.85
    elif "introduction" in section:
        base = 0.75
    if any(token in text.lower() for token in ("improve", "outperform", "reduce", "increase")):
        base += 0.04
    return min(1.0, round(base, 3))


def _overlap(left: str, right: str) -> int:
    def normalize(word: str) -> str:
        value = word.lower()
        if len(value) > 4 and value.endswith("s"):
            value = value[:-1]
        return value

    left_words = {normalize(word) for word in WORD_RE.findall(left)}
    right_words = {normalize(word) for word in WORD_RE.findall(right)}
    stop = {"the", "and", "with", "that", "this", "from", "are", "for", "our", "we"}
    return len((left_words - stop) & (right_words - stop))


def _dedupe_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    result: list[EvidenceItem] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return result


def _extract_profile(ledger: EvidenceLedger, groups: dict[str, list[EvidenceItem]]) -> PaperProfile:
    title_items = _matching_sections(groups, "title")
    title_item = title_items[0] if title_items else None
    abstract_items = _matching_sections(groups, "abstract")
    introduction_items = _matching_sections(groups, "introduction", "background")
    method_items = _matching_sections(groups, "method", "approach", "model")
    experiment_items = _matching_sections(groups, "experiment", "evaluation", "result")
    conclusion_items = _matching_sections(groups, "conclusion", "limitation", "discussion")

    abstract_text = " ".join(item.text for item in abstract_items).strip() or None
    research_question, research_evidence = _first_sentence(
        [*abstract_items, *introduction_items],
        ("we study", "we investigate", "research question", "we ask", "aim"),
    )
    contributions, contribution_evidence = _sentences_with_keywords(
        [*abstract_items, *introduction_items],
        ("we propose", "we introduce", "we present", "contribution"),
    )
    method_inputs, input_evidence = _first_sentence(method_items, ("input", "takes", "given"))
    method_outputs, output_evidence = _first_sentence(method_items, ("output", "produces", "returns"))
    method_assumptions, assumption_evidence = _first_sentence(
        method_items, ("assume", "assumption", "requires")
    )
    conclusions = [item.text for item in conclusion_items]
    boundaries, boundary_evidence = _sentences_with_keywords(
        conclusion_items,
        ("limited", "limitation", "only", "under", "future work", "but"),
    )

    domain_value = None
    domain_evidence: list[EvidenceItem] = []
    domain_candidates = [*abstract_items, *introduction_items, *method_items]
    combined = " ".join(item.text.lower() for item in domain_candidates)
    if any(token in combined for token in ("agent", "llm", "algorithm", "computer science")):
        domain_value = "computer_science"
        domain_evidence = domain_candidates[:1]
    paper_type_value = "empirical" if experiment_items else None
    paper_type_evidence = experiment_items[:1]

    section_roles_value = [
        {"section": section, "role": _section_role(section)}
        for section in groups
        if section
    ]
    section_role_evidence = [items[0] for section, items in groups.items() if section and items]
    warnings = list(ledger.metadata.get("warnings") or [])
    return PaperProfile(
        paper_id=ledger.paper_id,
        title=_reported(title_item.text if title_item else None, [title_item] if title_item else []),
        abstract=_reported(abstract_text, abstract_items),
        domain=_inferred(domain_value, domain_evidence),
        paper_type=_inferred(paper_type_value, paper_type_evidence),
        research_question=_reported(research_question, research_evidence),
        contributions=_reported(contributions, contribution_evidence),
        section_roles=_inferred(section_roles_value, section_role_evidence),
        method_inputs=_reported(method_inputs, input_evidence),
        method_outputs=_reported(method_outputs, output_evidence),
        method_assumptions=_reported(method_assumptions, assumption_evidence),
        conclusions=_reported(conclusions, conclusion_items),
        conclusion_boundaries=_reported(boundaries, boundary_evidence),
        parse_warnings=_reported([str(warning) for warning in warnings], []),
    )


def _section_role(section: str) -> str:
    if "abstract" in section:
        return "summary_and_primary_claims"
    if "introduction" in section:
        return "motivation_and_contributions"
    if any(token in section for token in ("method", "approach", "model")):
        return "method_definition"
    if any(token in section for token in ("experiment", "evaluation", "result")):
        return "empirical_support"
    if any(token in section for token in ("conclusion", "discussion", "limitation")):
        return "conclusions_and_boundaries"
    return "supporting_context"


def _extract_claim_graph(
    ledger: EvidenceLedger,
    groups: dict[str, list[EvidenceItem]],
    profile: PaperProfile,
) -> ClaimGraph:
    candidate_items = _matching_sections(
        groups, "abstract", "introduction", "result", "conclusion"
    )
    support_items = _matching_sections(
        groups, "method", "experiment", "evaluation", "result", "dataset"
    )
    claim_keywords = (
        "we propose",
        "we introduce",
        "we show",
        "we demonstrate",
        "we find",
        "outperform",
        "improve",
        "reduce",
        "achieve",
        "preserv",
    )
    claims_by_id: dict[str, ProfileClaim] = {}
    edges: list[ClaimSupportEdge] = []
    for item in candidate_items:
        for sentence in _sentences(item.text):
            if not any(keyword in sentence.lower() for keyword in claim_keywords):
                continue
            claim_id = _stable_claim_id(sentence)
            if claim_id in claims_by_id:
                existing = claims_by_id[claim_id]
                if item.id not in existing.evidence_ids:
                    existing.evidence_ids.append(item.id)
                continue
            supporting = [
                support
                for support in support_items
                if support.id != item.id and _overlap(sentence, support.text) >= 2
            ]
            conflicting_support = [
                support
                for support in supporting
                if any(
                    token in support.text.lower()
                    for token in ("does not", "did not", "failed to", "decrease", "worse", "conflict")
                )
            ]
            result_support = [
                support for support in supporting if "result" in _normalized_section(support.section)
            ]
            method_support = [
                support
                for support in supporting
                if any(
                    token in _normalized_section(support.section)
                    for token in ("method", "experiment", "evaluation", "dataset")
                )
            ]
            if conflicting_support:
                status = ClaimSupportStatus.CONFLICTING
            elif result_support and method_support:
                status = ClaimSupportStatus.SUPPORTED
            elif supporting:
                status = ClaimSupportStatus.PARTIALLY_SUPPORTED
            else:
                status = ClaimSupportStatus.INSUFFICIENT_EVIDENCE
            claim = ProfileClaim(
                claim_id=claim_id,
                text=sentence,
                claim_type=_claim_type(sentence),
                centrality=_claim_centrality(item, sentence),
                evidence_ids=[item.id],
                support_evidence_ids=[support.id for support in supporting],
                support_status=status,
                conclusion_boundaries=profile.conclusion_boundaries.model_copy(deep=True),
                benign_explanations=_inferred(
                    (
                        ["The paper may intend a narrower scope than the sentence states."]
                        if status is not ClaimSupportStatus.SUPPORTED
                        else []
                    ),
                    [item],
                ),
            )
            claims_by_id[claim_id] = claim
            edges.extend(
                ClaimSupportEdge(
                    source_evidence_id=support.id,
                    target_claim_id=claim_id,
                    evidence_ids=[support.id, item.id],
                )
                for support in supporting
            )
    claims = sorted(claims_by_id.values(), key=lambda claim: (-claim.centrality, claim.claim_id))
    return ClaimGraph(paper_id=ledger.paper_id, claims=claims, edges=edges)


def _field_from_items(items: list[EvidenceItem]) -> GroundedField:
    return _reported([item.text for item in items], items)


def _inferred_field(items: list[EvidenceItem]) -> GroundedField:
    return _inferred([item.text for item in items], items)


def _choose_experiment_items(
    items: list[EvidenceItem],
    keywords: tuple[str, ...],
) -> list[EvidenceItem]:
    return [
        item
        for item in items
        if any(keyword in item.text.lower() for keyword in keywords)
    ]


def _associated_media(
    media: list[EvidenceItem],
    *,
    section: str,
    base_items: list[EvidenceItem],
    single_experiment: bool,
) -> list[EvidenceItem]:
    associated: list[EvidenceItem] = []
    for item in media:
        media_section = _normalized_section(item.section)
        if media_section == section:
            associated.append(item)
            continue
        if any(token in media_section for token in ("experiment", "evaluation")):
            continue
        if single_experiment and "result" in media_section:
            associated.append(item)
            continue
        if any(_overlap(item.text, evidence.text) >= 1 for evidence in base_items):
            associated.append(item)
    return associated


def _extract_experiments(
    ledger: EvidenceLedger,
    groups: dict[str, list[EvidenceItem]],
) -> ExperimentInventory:
    specific_groups = [
        (
            section,
            [
                item
                for item in items
                if item.type
                not in {EvidenceType.FIGURE_CAPTION, EvidenceType.TABLE, EvidenceType.TABLE_CELL}
            ],
        )
        for section, items in groups.items()
        if any(token in section for token in ("experiment", "evaluation"))
        and any(
            item.type
            not in {EvidenceType.FIGURE_CAPTION, EvidenceType.TABLE, EvidenceType.TABLE_CELL}
            for item in items
        )
    ]
    result_items = _matching_sections(groups, "result")
    data_items = [
        item
        for section, items in groups.items()
        if section == "data" or "dataset" in section or "data " in section
        for item in items
    ]
    if not specific_groups and result_items:
        specific_groups = [("results", result_items)]
        result_items = []
    if not specific_groups:
        return ExperimentInventory(paper_id=ledger.paper_id)

    figures = [item for item in ledger.items if item.type is EvidenceType.FIGURE_CAPTION]
    tables = [item for item in ledger.items if item.type in {EvidenceType.TABLE, EvidenceType.TABLE_CELL}]
    experiments: list[ExperimentRecord] = []
    for section, base_items in sorted(specific_groups, key=lambda value: value[0]):
        related_results = [
            item
            for item in result_items
            if len(specific_groups) == 1
            or any(_overlap(item.text, base.text) >= 1 for base in base_items)
        ]
        related_data = [
            item
            for item in data_items
            if len(specific_groups) == 1
            or any(_overlap(item.text, base.text) >= 1 for base in base_items)
        ]
        experiment_items = _dedupe_items([*base_items, *related_data, *related_results])
        associated_figures = _associated_media(
            figures,
            section=section,
            base_items=base_items,
            single_experiment=len(specific_groups) == 1,
        )
        associated_tables = _associated_media(
            tables,
            section=section,
            base_items=base_items,
            single_experiment=len(specific_groups) == 1,
        )
        datasets = _choose_experiment_items(
            experiment_items,
            ("dataset", "benchmark", "corpus"),
        )
        metrics = _choose_experiment_items(experiment_items, ("metric",))
        datasets_field = (
            _field_from_items(datasets)
            if datasets
            else _inferred_field(
                [
                    item
                    for item in [*associated_figures, *associated_tables]
                    if any(token in item.text.lower() for token in ("dataset", "benchmark", " on "))
                ]
            )
        )
        metrics_field = (
            _field_from_items(metrics)
            if metrics
            else _inferred_field(
                [
                    item
                    for item in [*associated_figures, *associated_tables]
                    if any(
                        token in item.text.lower()
                        for token in ("precision", "recall", "accuracy", "f1", "auc")
                    )
                ]
            )
        )
        experiment = ExperimentRecord(
            experiment_id=f"experiment-{hashlib.sha256(section.encode('utf-8')).hexdigest()[:10]}",
            label=_inferred(section, base_items[:1]),
            datasets=datasets_field,
            sample_sizes=_field_from_items(
                _choose_experiment_items(
                    experiment_items,
                    ("n=", "sample", "papers", "participants"),
                )
            ),
            data_splits=_field_from_items(
                _choose_experiment_items(
                    experiment_items,
                    ("split", "train", "validation", "test set"),
                )
            ),
            baselines=_field_from_items(
                _choose_experiment_items(
                    experiment_items,
                    ("baseline", "compared with", "comparison"),
                )
            ),
            metrics=metrics_field,
            random_seeds=_field_from_items(
                _choose_experiment_items(experiment_items, ("seed", "random state"))
            ),
            statistics=_field_from_items(
                _choose_experiment_items(
                    experiment_items,
                    ("confidence", "p-value", "standard deviation", "std"),
                )
            ),
            ablations=_field_from_items(
                _choose_experiment_items(
                    experiment_items,
                    ("ablation", "remove", "without"),
                )
            ),
            key_figures=_field_from_items(associated_figures),
            key_tables=_field_from_items(associated_tables),
        )
        populated = sum(
            field.value not in (None, "", [], {}) for field in experiment.grounded_fields()
        )
        experiment.importance_score = min(1.0, round(0.45 + populated * 0.055, 3))
        experiments.append(experiment)
    return ExperimentInventory(paper_id=ledger.paper_id, experiments=experiments)


def _build_review_plan(
    ledger: EvidenceLedger,
    claim_graph: ClaimGraph,
    inventory: ExperimentInventory,
) -> ReviewPlan:
    core_claims = [claim for claim in claim_graph.claims if claim.centrality >= 0.8][:3]
    if not core_claims and claim_graph.claims:
        core_claims = claim_graph.claims[:1]
    core_ids = [claim.claim_id for claim in core_claims]
    route = [
        ReviewRouteItem(
            rank=index,
            priority="core" if claim.claim_id in core_ids else "supporting",
            reason=(
                "High-centrality claim with incomplete support."
                if claim.support_status is not ClaimSupportStatus.SUPPORTED
                else "High-centrality claim and its supporting evidence."
            ),
            claim_ids=[claim.claim_id],
            evidence_ids=[*claim.evidence_ids, *claim.support_evidence_ids],
        )
        for index, claim in enumerate(claim_graph.claims, start=1)
    ]
    next_rank = len(route) + 1
    for experiment in sorted(
        inventory.experiments,
        key=lambda item: (-item.importance_score, item.experiment_id),
    ):
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for field in experiment.grounded_fields()
                for evidence_id in field.evidence_ids
            )
        )
        route.append(
            ReviewRouteItem(
                rank=next_rank,
                priority="core" if experiment.importance_score >= 0.7 else "supporting",
                reason="Inspect experiment design and its support for the central claims.",
                claim_ids=core_ids,
                evidence_ids=evidence_ids,
            )
        )
        next_rank += 1
    parse_warnings = list(ledger.metadata.get("warnings") or [])
    if parse_warnings:
        route.append(
            ReviewRouteItem(
                rank=next_rank,
                priority="core",
                reason="Resolve parser uncertainty before relying on affected evidence.",
                claim_ids=core_ids,
                evidence_ids=[],
            )
        )
    all_evidence = list(
        dict.fromkeys(
            evidence_id
            for claim in claim_graph.claims
            for evidence_id in [*claim.evidence_ids, *claim.support_evidence_ids]
        )
    )
    assignments = [
        AgentReviewAssignment(
            agent_id="structure",
            focus="research question, contributions, and argument structure",
            claim_ids=core_ids,
            evidence_ids=[item.id for item in ledger.items if "abstract" in item.section.lower()],
        ),
        AgentReviewAssignment(
            agent_id="method",
            focus="method inputs, outputs, assumptions, and support for core claims",
            claim_ids=core_ids,
            evidence_ids=all_evidence,
        ),
    ]
    if inventory.experiments:
        assignments.extend(
            [
                AgentReviewAssignment(
                    agent_id="experiment",
                    focus="datasets, baselines, metrics, ablations, figures, and tables",
                    claim_ids=core_ids,
                    evidence_ids=list(
                        dict.fromkeys(
                            evidence_id
                            for experiment in inventory.experiments
                            for field in experiment.grounded_fields()
                            for evidence_id in field.evidence_ids
                        )
                    ),
                ),
                AgentReviewAssignment(
                    agent_id="statistics",
                    focus="sample sizes, seeds, uncertainty, and statistical reporting",
                    claim_ids=core_ids,
                    evidence_ids=list(
                        dict.fromkeys(
                            evidence_id
                            for experiment in inventory.experiments
                            for field in (
                                experiment.sample_sizes,
                                experiment.random_seeds,
                                experiment.statistics,
                            )
                            for evidence_id in field.evidence_ids
                        )
                    ),
                ),
            ]
        )
    collapsed_ids = [
        item.id
        for item in ledger.items
        if str(item.metadata.get("importance") or "").lower() == "minor"
        or any(token in _normalized_section(item.section) for token in ("writing", "style"))
    ]
    return ReviewPlan(
        paper_id=ledger.paper_id,
        core_claim_ids=core_ids,
        reading_route=route,
        agent_assignments=assignments,
        collapsed_evidence_ids=collapsed_ids,
    )


def build_paper_understanding(
    ledger: EvidenceLedger,
    output_dir: Path,
) -> PaperUnderstandingArtifacts:
    """Build and persist the four deterministic paper-understanding artifacts."""

    validated = EvidenceLedger.model_validate(ledger)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = _section_groups(validated)
    profile = _extract_profile(validated, groups)
    claim_graph = _extract_claim_graph(validated, groups, profile)
    inventory = _extract_experiments(validated, groups)
    review_plan = _build_review_plan(validated, claim_graph, inventory)
    artifacts = {
        "paper_profile": output_dir / "paper_profile.json",
        "claim_graph": output_dir / "claim_graph.json",
        "experiment_inventory": output_dir / "experiment_inventory.json",
        "review_plan": output_dir / "review_plan.json",
    }
    write_json_atomic(artifacts["paper_profile"], profile.model_dump(mode="json"))
    write_json_atomic(artifacts["claim_graph"], claim_graph.model_dump(mode="json"))
    write_json_atomic(
        artifacts["experiment_inventory"], inventory.model_dump(mode="json")
    )
    write_json_atomic(artifacts["review_plan"], review_plan.model_dump(mode="json"))
    return PaperUnderstandingArtifacts(
        profile=profile,
        claim_graph=claim_graph,
        experiment_inventory=inventory,
        review_plan=review_plan,
        artifact_paths={name: str(path.resolve()) for name, path in artifacts.items()},
    )

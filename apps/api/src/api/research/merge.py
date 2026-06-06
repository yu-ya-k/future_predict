from __future__ import annotations

from pydantic import BaseModel, Field


class RegressionError(RuntimeError):
    pass


class PatchDelta(BaseModel):
    target_item_id: str
    section_id: str
    operation: str
    new_text: str
    citation_ids: list[str] = Field(default_factory=list)
    patch_reason: str


class ReportDocument(BaseModel):
    sections: dict[str, str]
    mutable_sections: set[str]
    preserve_section_ids: set[str] = Field(default_factory=set)


def deterministic_merge(
    report: ReportDocument,
    patches: list[PatchDelta],
) -> ReportDocument:
    sections = dict(report.sections)
    for patch in patches:
        if patch.section_id in report.preserve_section_ids:
            raise RegressionError(f"Preserved section changed: {patch.section_id}")

        if patch.operation in {"replace_section", "append_to_section"}:
            if patch.section_id not in report.mutable_sections:
                raise RegressionError(f"Section is not mutable: {patch.section_id}")
            if patch.section_id not in sections:
                raise RegressionError(f"Section does not exist: {patch.section_id}")

        if patch.operation == "replace_section":
            sections[patch.section_id] = patch.new_text
        elif patch.operation == "append_to_section":
            sections[patch.section_id] = (
                sections[patch.section_id].rstrip() + "\n\n" + patch.new_text
            )
        elif patch.operation == "add_new_section":
            if patch.section_id in report.preserve_section_ids:
                raise RegressionError(f"Cannot add over preserved section: {patch.section_id}")
            sections[patch.section_id] = patch.new_text
        else:
            raise RegressionError(f"Unsupported patch operation: {patch.operation}")

    return report.model_copy(update={"sections": sections})

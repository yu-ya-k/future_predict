from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from api.research.schemas import Citation, ToolCallSummary


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        mapping = cast(Mapping[str, Any], obj)
        return mapping.get(name, default)
    return getattr(obj, name, default)


def _iter(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(cast(list[Any] | tuple[Any, ...], value))
    return []


def _first_present(obj: Any, *names: str) -> Any:
    for name in names:
        value = _get(obj, name)
        if value is not None:
            return value
    return None


def response_to_jsonable(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return dict(cast(Mapping[str, Any], response))

    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(response, method_name, None)
        if callable(method):
            result = method()
            if isinstance(result, dict):
                return dict(cast(Mapping[str, Any], result))

    return {
        "id": _get(response, "id"),
        "status": _get(response, "status"),
        "output_text": _get(response, "output_text"),
        "error": _get(response, "error"),
        "incomplete_details": _get(response, "incomplete_details"),
    }


def get_response_id(response: Any) -> str:
    value = _get(response, "id", "")
    return str(value or "")


def get_response_status(response: Any) -> str:
    value = _get(response, "status", "")
    return str(value or "")


def get_response_output_text(response: Any) -> str:
    value = _get(response, "output_text", "")
    if value:
        return str(value)

    texts: list[str] = []
    for item in _iter(_get(response, "output")):
        for block in _iter(_get(item, "content")):
            block_type = _get(block, "type")
            text = _get(block, "text")
            if block_type in {"output_text", "text"} and text:
                texts.append(str(text))

    return "\n".join(texts)


def extract_citations(response: Any) -> list[Citation]:
    citations: list[Citation] = []

    for item in _iter(_get(response, "output")):
        for block in _iter(_get(item, "content")):
            for annotation in _iter(_get(block, "annotations")):
                annotation_type = _get(annotation, "type")
                url = _first_present(annotation, "url", "uri")
                title = _citation_title(annotation)
                if annotation_type in {
                    "url_citation",
                    "citation",
                    "file_citation",
                    "container_file_citation",
                    "file_path",
                } or _looks_like_citation(annotation):
                    citations.append(
                        Citation(
                            url=url,
                            title=title,
                            start_index=_get(annotation, "start_index"),
                            end_index=_get(annotation, "end_index"),
                            source_type=str(annotation_type or "citation"),
                        )
                    )

    return citations


def extract_tool_calls(response: Any) -> list[ToolCallSummary]:
    calls: list[ToolCallSummary] = []

    for item in _iter(_get(response, "output")):
        item_type = _get(item, "type")
        if not item_type or "call" not in str(item_type):
            continue

        calls.append(
            ToolCallSummary(
                type=str(item_type),
                status=_get(item, "status"),
                query=_extract_tool_query(item),
                url=_first_present(item, "url", "target_url")
                or _first_present(_get(item, "action"), "url", "target_url"),
                server_label=_first_present(item, "server_label", "server")
                or _get(_get(item, "action"), "server_label"),
            )
        )

    return calls


def _extract_tool_query(item: Any) -> str | None:
    action = _get(item, "action")
    value = (
        _get(item, "query")
        or _get(item, "search_query")
        or _get(item, "search_terms")
        or _get(action, "query")
        or _get(action, "search_query")
        or _get(action, "search_terms")
    )
    if value is None:
        return None
    return str(value)


def _looks_like_citation(annotation: Any) -> bool:
    return any(
        _get(annotation, key) is not None
        for key in (
            "url",
            "uri",
            "title",
            "filename",
            "file_name",
            "file_id",
            "container_id",
            "start_index",
            "end_index",
        )
    )


def _citation_title(annotation: Any) -> str | None:
    value = _first_present(annotation, "title", "filename", "file_name", "file_id")
    if value is None:
        return None
    return str(value)

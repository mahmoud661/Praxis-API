"""Schema resolution and merging utilities for LangGraph agent middleware.

Handles merging multiple middleware schemas and respecting omit flags.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Iterable, get_args, get_origin, get_type_hints

from langchain.agents.middleware.types import AgentMiddleware
from typing_extensions import NotRequired, Required, TypedDict

if TYPE_CHECKING:
    from langchain.agents.middleware.types import JumpTo


def resolve_schema(schemas: Iterable[type], schema_name: str, omit_flag: str | None = None) -> type:
    """Resolve schema by merging schemas and optionally respecting `OmitFromSchema` annotations.

    Args:
        schemas: List of schema types to merge
        schema_name: Name for the generated `TypedDict`
        omit_flag: If specified, omit fields with this flag set (`'input'` or
            `'output'`)
    """
    from langchain.agents.middleware.types import OmitFromSchema

    all_annotations = {}

    for schema in schemas:
        hints = get_type_hints(schema, include_extras=True)

        for field_name, field_type in hints.items():
            should_omit = False

            if omit_flag:
                # Check for omission in the annotation metadata
                metadata = extract_metadata(field_type)
                for meta in metadata:
                    if isinstance(meta, OmitFromSchema) and getattr(meta, omit_flag) is True:
                        should_omit = True
                        break

            if not should_omit:
                if field_name not in all_annotations:
                    all_annotations[field_name] = field_type

    return TypedDict(schema_name, all_annotations)  # type: ignore[operator]


def extract_metadata(type_: type) -> list:
    """Extract metadata from a field type, handling Required/NotRequired and Annotated wrappers."""
    # Handle Required[Annotated[...]] or NotRequired[Annotated[...]]
    if get_origin(type_) in (Required, NotRequired):
        inner_type = get_args(type_)[0]
        if get_origin(inner_type) is Annotated:
            return list(get_args(inner_type)[1:])

    # Handle direct Annotated[...]
    elif get_origin(type_) is Annotated:
        return list(get_args(type_)[1:])

    return []


def get_can_jump_to(middleware: AgentMiddleware[Any, Any], hook_name: str) -> list[JumpTo]:
    """Get the `can_jump_to` list from either sync or async hook methods.

    Args:
        middleware: The middleware instance to inspect.
        hook_name: The name of the hook (`'before_model'` or `'after_model'`).

    Returns:
        List of jump destinations, or empty list if not configured.
    """

    # Get the base class method for comparison
    base_sync_method = getattr(AgentMiddleware, hook_name, None)
    base_async_method = getattr(AgentMiddleware, f"a{hook_name}", None)

    # Try sync method first - only if it's overridden from base class
    sync_method = getattr(middleware.__class__, hook_name, None)
    if sync_method and sync_method is not base_sync_method and hasattr(sync_method, "__can_jump_to__"):
        return sync_method.__can_jump_to__

    # Try async method - only if it's overridden from base class
    async_method = getattr(middleware.__class__, f"a{hook_name}", None)
    if async_method and async_method is not base_async_method and hasattr(async_method, "__can_jump_to__"):
        return async_method.__can_jump_to__

    return []

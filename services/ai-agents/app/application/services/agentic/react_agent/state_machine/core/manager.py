"""Section manager for validating and executing section transitions."""

from __future__ import annotations

from typing import Any

from react_agent.state_machine.types.config_types import SectionConfig
from react_agent.state_machine.types.type_aliases import SectionName
import logging
logger = logging.getLogger(__name__)


class SectionManager:
    """Manages section configurations and transitions."""

    def __init__(
        self,
        sections: dict[SectionName, SectionConfig],
        initial_section: SectionName,
        strict_validation: bool = True,
        fallback_section: SectionName | None = None,
    ):
        """Initialize section manager.

        Args:
            sections: Dictionary of section configurations
            initial_section: Starting section name
            strict_validation: Whether to enforce strict validation
            fallback_section: Section to use if current section is not found (defaults to initial_section)
        """
        if not sections:
            raise ValueError("At least one section must be defined")
        if initial_section not in sections:
            raise ValueError(f"Initial section '{initial_section}' not found in sections")

        self.sections = sections
        self.initial_section = initial_section
        self.strict_validation = strict_validation

        if fallback_section is None:
            logger.warning(
                f"No fallback_section specified. Using initial_section '{initial_section}' as fallback. "
                f"Consider explicitly setting fallback_section to handle removed sections in production."
            )
            self.fallback_section = initial_section
        else:
            self.fallback_section = fallback_section

        if self.fallback_section not in sections:
            raise ValueError(f"Fallback section '{self.fallback_section}' not found in sections")

    def get_section(self, name: SectionName) -> SectionConfig | None:
        """Get section config by name."""
        return self.sections.get(name)

    def get_section_with_fallback(self, name: SectionName | None) -> tuple[SectionConfig, bool]:
        """Get section config with fallback if not found.

        Returns:
            Tuple of (section_config, used_fallback)
        """
        if name and (config := self.sections.get(name)):
            return config, False

        logger.warning(
            f"Section '{name}' not found, using fallback '{self.fallback_section}'. "
            f"This may indicate a removed section that users still have in state."
        )
        return self.sections[self.fallback_section], True

    def get_current_section_config(self, state: dict[str, Any]) -> SectionConfig | None:
        """Get current section config."""
        current = state.get("current_section")
        return self.get_section(current) if current else None

    def can_transition(
        self, from_section: SectionName, to_section: SectionName, state: dict[str, Any], strict: bool | None = None
    ) -> tuple[bool, str | None]:
        """Check if transition is allowed."""
        use_strict = strict if strict is not None else self.strict_validation

        from_config = self.get_section(from_section)
        to_config = self.get_section(to_section)

        if not from_config:
            return False, f"Source section '{from_section}' not found"
        if not to_config:
            return False, f"Target section '{to_section}' not found"

        # Check allowed transitions
        if not from_config.can_transition_to(to_section):
            if use_strict and from_config.strict_validation:
                return False, f"Transition '{from_section}' -> '{to_section}' not allowed"
            logger.warning(f"Non-strict transition: {from_section} -> {to_section}")

        # Validate required fields
        if use_strict and to_config.strict_validation:
            valid, missing = to_config.validate_required_fields(state)
            if not valid:
                return False, f"Missing fields for '{to_section}': {missing}"

        return True, None

    def evaluate_auto_transitions(self, state: dict[str, Any]) -> SectionName | None:
        """Evaluate auto-transition conditions for the CURRENT section ONLY."""
        current = state.get("current_section") or self.initial_section

        # Get the current section's config
        config = self.get_current_section_config(state)
        if not config:
            return None

        # IMPORTANT: Only evaluate auto-transitions for the current section
        # This ensures auto-transitions don't trigger when user is in other sections

        # Check what target the condition evaluates to
        target = config.evaluate_auto_transitions(state)

        if target:
            can_trans, error = self.can_transition(current, target, state)
            if not can_trans:
                logger.warning(f"[AUTO_TRANSITION] ❌ Auto-transition blocked: {current} → {target}. {error}")
                return None

        return target

    def get_section_names(self) -> list[SectionName]:
        """Get all section names."""
        return list(self.sections.keys())


__all__ = ["SectionManager"]

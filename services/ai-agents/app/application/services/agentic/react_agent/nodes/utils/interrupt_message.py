"""Interrupt message type for human-in-the-loop workflow control."""

from typing import Any, Dict, Literal, Optional

from langchain_core.messages import ChatMessage


class InterruptMessage(ChatMessage):
    """Message type for human-in-the-loop interrupts.

    This message type is used to record when the workflow paused for human approval
    and what decision was made. These messages are filtered out before being sent
    to the AI to prevent confusion.

    Inherits from ChatMessage with role="interrupt" so it's compatible with
    LangChain's message utilities like get_buffer_string.

    Attributes:
        role: Always set to "interrupt" (inherited from ChatMessage)
        content: The interrupt reason/description shown to the user
        reason: Description of why the interrupt occurred
        action: The tool/action name that triggered the interrupt
        options: Available response options presented to the user
        draft: Optional draft artifact ID for approval workflows
        user_response: The user's decision (filled after resume)
    """

    role: Literal["interrupt"] = "interrupt"
    type: Literal["interrupt"] = "interrupt"  # Override parent's type for discriminated union
    reason: str
    action: str
    options: Dict[str, str]
    draft: Optional[str] = None
    user_response: Optional[Dict[str, Any]] = None

    def __init__(self, content: Optional[Any] = None, **data: Any):
        """Initialize with computed content from interrupt fields."""
        # Handle content passed as positional or keyword argument
        if content is not None:
            data["content"] = content

        # Extract interrupt-specific fields
        reason = data.get("reason", "")
        action = data.get("action", "")
        options = data.get("options", {})
        draft = data.get("draft")
        user_response = data.get("user_response")

        # Ensure role is set to "interrupt" (required by ChatMessage)
        data["role"] = "interrupt"

        # Build content if not provided
        if "content" not in data:
            data["content"] = [
                {
                    "type": "interrupt",
                    "reason": reason,
                    "action": action,
                    "options": options,
                    "draft": draft,
                }
            ]

        # Ensure additional_kwargs has message_type
        if "additional_kwargs" not in data:
            data["additional_kwargs"] = {}

        if "message_type" not in data["additional_kwargs"]:
            data["additional_kwargs"]["message_type"] = "interrupt"

        if user_response and "user_response" not in data["additional_kwargs"]:
            data["additional_kwargs"]["user_response"] = user_response

        # Call parent init with content as keyword argument
        super().__init__(content=data.pop("content"), **data)

    def update_with_response(
        self,
        decision: bool,
        response_type: str = "accept",
        args: Any = None,
    ) -> None:
        """Update the interrupt message with the user's response.

        Args:
            decision: True if user approved, False if rejected
            response_type: Type of response ("accept", "edit", "response")
            args: Additional arguments or feedback from the user
        """
        response_data = {
            "decision": decision,
            "type": response_type,
            "args": args,
        }

        # Use object.__setattr__ to bypass Pydantic's frozen behavior if enabled
        object.__setattr__(self, "user_response", response_data)

        if not self.additional_kwargs:
            object.__setattr__(self, "additional_kwargs", {})

        self.additional_kwargs["user_response"] = response_data

    @classmethod
    def create(
        cls,
        reason: str,
        action: str,
        options: Dict[str, str],
        draft: Optional[str] = None,
        current_agent: Optional[str] = None,
    ) -> "InterruptMessage":
        """Factory method to create an InterruptMessage.

        Args:
            reason: Description of why the interrupt occurred
            action: The tool/action name that triggered the interrupt
            options: Available response options
            draft: Optional draft artifact ID
            current_agent: Optional current agent name for tracking

        Returns:
            New InterruptMessage instance
        """
        kwargs = {}
        if current_agent:
            kwargs["additional_kwargs"] = {
                "responsible_agent": {"agent": current_agent},
                "message_type": "interrupt",
            }

        return cls(reason=reason, action=action, options=options, draft=draft, **kwargs)

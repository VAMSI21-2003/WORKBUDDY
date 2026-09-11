"""Eyes — perceives raw input from the user and the workspace."""

import logging

from buddy.state import BuddyState

logger = logging.getLogger(__name__)


class Eyes:
    """The Eyes stage of Buddy's cognitive cycle.

    Eyes is responsible only for collecting information already present
    on ``BuddyState`` — the user's message, uploaded files, the workspace
    and session this turn belongs to, and any attached metadata — and
    recording each as a plain observation. It does not interpret what any
    of it means, does not reason about intent, and does not plan; those
    responsibilities belong to Brain, Think, and Planner respectively
    (see ``docs/WORKBUDDY_ARCHITECTURE.md`` Section 4). Eyes has no
    dependency on any AI model — it is a purely mechanical recording step.
    """

    def __call__(self, state: BuddyState) -> BuddyState:
        """Collect observations from ``state`` and record them.

        Args:
            state: The current ``BuddyState`` for this turn.

        Returns:
            The same state, with this turn's observations appended.
        """
        self._observe_session(state)
        self._observe_workspace(state)
        self._observe_user_message(state)
        self._observe_uploaded_files(state)
        self._observe_metadata(state)
        logger.debug(
            "Eyes recorded %d observation(s) for session %r",
            len(state.observations),
            state.session_id,
        )
        return state

    @staticmethod
    def _observe_session(state: BuddyState) -> None:
        """Record the session this turn belongs to."""
        state.add_observation(f"session_id: {state.session_id}")

    @staticmethod
    def _observe_workspace(state: BuddyState) -> None:
        """Record the workspace this turn is scoped to."""
        state.add_observation(f"workspace_id: {state.workspace_id}")

    @staticmethod
    def _observe_user_message(state: BuddyState) -> None:
        """Record the raw user message received for this turn, if any."""
        if state.user_message:
            state.add_observation(f"user_message: {state.user_message!r}")
        else:
            state.add_observation("user_message: <empty>")

    @staticmethod
    def _observe_uploaded_files(state: BuddyState) -> None:
        """Record every file currently available in the workspace."""
        if not state.uploaded_files:
            state.add_observation("uploaded_files: none")
            return
        for file_reference in state.uploaded_files:
            state.add_observation(f"uploaded_file: {file_reference}")

    @staticmethod
    def _observe_metadata(state: BuddyState) -> None:
        """Record each piece of auxiliary metadata attached to the state."""
        if not state.metadata:
            return
        for key, value in state.metadata.items():
            state.add_observation(f"metadata: {key}={value!r}")

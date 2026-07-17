"""start of the template to build scenario for Proactive Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from are.simulation.apps import SandboxLocalFileSystem
from are.simulation.scenarios.scenario import ScenarioStatus, ScenarioValidationResult
from are.simulation.types import AbstractEnvironment, Event, EventRegisterer

# TODO: import all Apps that will be used in this scenario
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
from pare.apps import (
    HomeScreenSystemApp,
    PAREAgentUserInterface,
)
from pare.scenarios import PAREScenario
from pare.scenarios.utils.registry import register_scenario

# TODO: replace these with paths from the resolved VisualAssetSpec / asset manifest.
# WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
SCENARIO_ASSET_DIR = Path(__file__).parent / "assets"


@register_scenario("original_scenario_id")
class ScenarioName(PAREScenario):
    """<<scenario_description>>."""

    start_time = datetime(2025, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
    status = ScenarioStatus.Draft
    is_benchmark_ready = False

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        # WARNING: this part is responsible to and can be modified only by Apps & Data Setup Agent
        """Initialize apps with test data."""
        self.agent_ui = PAREAgentUserInterface()
        self.system_app = HomeScreenSystemApp(name="System")
        self.files = SandboxLocalFileSystem(name="Files")

        # TODO: Initialize scenario specific apps here.
        # Visual assets should be loaded from the asset manifest / resolved asset directory,
        # written into self.files with jpeg_bytes_for_sandbox(...), and then attached
        # through Email, Album, Notes, or Files according to the VisualAssetSpec.

        # TODO: Populate apps with scenario specific data here.
        # Example pattern:
        # local_image_path = SCENARIO_ASSET_DIR / "<<asset_filename>>"
        # with self.files.open("<<sandbox_path>>", "wb") as f:
        #     f.write(jpeg_bytes_for_sandbox(local_image_path.read_bytes()))

        # TODO: Register all apps here in self.apps
        self.apps = [self.agent_ui, self.system_app, self.files]

    def build_events_flow(self) -> None:
        # WARNING: this part is responsible to and can be modified only by events-flow agent
        """Build event flow - environment events with agent detection and agent actions."""
        # TODO: initialize all apps from self.apps like aui and system_app below
        aui = self.get_typed_app(PAREAgentUserInterface)
        system_app = self.get_typed_app(HomeScreenSystemApp, "System")
        files = self.get_typed_app(SandboxLocalFileSystem, "Files")

        with EventRegisterer.capture_mode():
            # TODO: Add environment events here

            # TODO: Add oracle events here
            # -- Agent will detect environment events, check App state changes(if necessary), send proposal to user via aui.send_message_to_user(...)
            # -- Agent must perform an image inspection step before any visually grounded proposal/action
            #    (for example files.display(...), email_app.get_email_by_id(...), or album_app.view_photo(...)).
            # -- User will choose to accept the Agent proposal via aui.accept_proposal(...)
            # -- Agent will again detect environment events(if has), check App state changes(if necessary), and interacts with available methods in Apps based on its findings

            pass

        # TODO: Register ALL events here in self.events
        self.events: list[Event] = []

    def validate(self, env: AbstractEnvironment) -> ScenarioValidationResult:
        # WARNING: this part is responsible to and can be modified only by validation agent
        """Validate that agent detects the environment events and made actions accordingly."""
        try:
            log_entries = env.event_log.list_view()
            allow_any_event_type = bool(getattr(env, "oracle_mode", False))

            # TODO: Check Step 1: Agent sent proposal to the user
            # example: proposal_found = ...

            # TODO: Check Step 2: Agent performed required image inspection before visually grounded action
            # example:
            # photo_visual_input_found = log_has_agent_image_view(
            #     log_entries,
            #     allow_any_event_type=allow_any_event_type,
            #     image_path="<<sandbox_path>>",
            # )

            # TODO: Check Step 3(contains one or more checks based on Agent actions): Agent's actions -- Agent interacted with methods in Apps based on previous visual findings
            # example: execute_action1_found = ...

            # TODO: get the success result
            # example: success = (proposal_found and detect_action1_found and execute_action1_found and ...)
            success = True
            return ScenarioValidationResult(success=success)

        except Exception as e:
            return ScenarioValidationResult(success=False, exception=e)


"""end of the template to build scenario for Proactive Agent."""

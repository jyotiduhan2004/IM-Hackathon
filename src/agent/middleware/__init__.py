"""Agent middleware for the compile pipeline.

Each middleware is additive — append to the list on `create_deep_agent`
rather than replacing defaults. See `compiler.create_compiler` for wiring.
"""

from src.agent.middleware.check_my_work_gate import GATE_REJECT_MESSAGE
from src.agent.middleware.check_my_work_gate import GATE_REJECT_PAT
from src.agent.middleware.check_my_work_gate import POST_WRITE_NUDGE_MESSAGE
from src.agent.middleware.check_my_work_gate import POST_WRITE_NUDGE_PAT
from src.agent.middleware.check_my_work_gate import CheckMyWorkGateMiddleware
from src.agent.middleware.chronological_scope import ChronologicalScopeMiddleware
from src.agent.middleware.edit_payload_sanity import EditPayloadSanityMiddleware
from src.agent.middleware.edit_staleness import EditStalenessMiddleware
from src.agent.middleware.entity_write_autoheal import EntityWriteAutohealMiddleware
from src.agent.middleware.glob_narrowing import GlobNarrowingMiddleware
from src.agent.middleware.legacy_page_hint import LegacyPageHintMiddleware
from src.agent.middleware.path_autoheal import PathAutohealMiddleware
from src.agent.middleware.read_file_truncation_hint import ReadFileTruncationHintMiddleware
from src.agent.middleware.reconnaissance_paralysis import RECONNAISSANCE_NUDGE_MESSAGE
from src.agent.middleware.reconnaissance_paralysis import ReconnaissanceParalysisMiddleware
from src.agent.middleware.same_thread_topic_guard import SameThreadTopicGuardMiddleware
from src.agent.middleware.sibling_draft_check import SiblingDraftCheckMiddleware
from src.agent.middleware.stuck_heartbeat import StuckHeartbeatMiddleware
from src.agent.middleware.stuck_heartbeat import StuckHeartbeatState
from src.agent.middleware.terminal_decision_guard import TERMINAL_NUDGE_MESSAGE
from src.agent.middleware.terminal_decision_guard import TerminalDecisionGuardMiddleware

__all__ = [
    "GATE_REJECT_MESSAGE",
    "GATE_REJECT_PAT",
    "POST_WRITE_NUDGE_MESSAGE",
    "POST_WRITE_NUDGE_PAT",
    "RECONNAISSANCE_NUDGE_MESSAGE",
    "TERMINAL_NUDGE_MESSAGE",
    "CheckMyWorkGateMiddleware",
    "ChronologicalScopeMiddleware",
    "EditPayloadSanityMiddleware",
    "EditStalenessMiddleware",
    "EntityWriteAutohealMiddleware",
    "GlobNarrowingMiddleware",
    "LegacyPageHintMiddleware",
    "PathAutohealMiddleware",
    "ReadFileTruncationHintMiddleware",
    "ReconnaissanceParalysisMiddleware",
    "SameThreadTopicGuardMiddleware",
    "SiblingDraftCheckMiddleware",
    "StuckHeartbeatMiddleware",
    "StuckHeartbeatState",
    "TerminalDecisionGuardMiddleware",
]

"""Agent middleware for the compile pipeline.

Each middleware is additive — append to the list on `create_deep_agent`
rather than replacing defaults. See `compiler.create_compiler` for wiring.
"""

from src.compile.middleware.check_my_work_gate import GATE_REJECT_MESSAGE
from src.compile.middleware.check_my_work_gate import GATE_REJECT_PAT
from src.compile.middleware.check_my_work_gate import POST_WRITE_NUDGE_MESSAGE
from src.compile.middleware.check_my_work_gate import POST_WRITE_NUDGE_PAT
from src.compile.middleware.check_my_work_gate import CheckMyWorkGateMiddleware
from src.compile.middleware.chronological_scope import ChronologicalScopeMiddleware
from src.compile.middleware.edit_payload_sanity import EditPayloadSanityMiddleware
from src.compile.middleware.edit_staleness import EditStalenessMiddleware
from src.compile.middleware.entity_write_autoheal import EntityWriteAutohealMiddleware
from src.compile.middleware.glob_narrowing import GlobNarrowingMiddleware
from src.compile.middleware.legacy_page_hint import LegacyPageHintMiddleware
from src.compile.middleware.path_autoheal import PathAutohealMiddleware
from src.compile.middleware.read_file_truncation_hint import ReadFileTruncationHintMiddleware
from src.compile.middleware.reconnaissance_paralysis import RECONNAISSANCE_NUDGE_MESSAGE
from src.compile.middleware.reconnaissance_paralysis import ReconnaissanceParalysisMiddleware
from src.compile.middleware.same_thread_topic_guard import SameThreadTopicGuardMiddleware
from src.compile.middleware.sibling_draft_check import SiblingDraftCheckMiddleware
from src.compile.middleware.terminal_decision_guard import TERMINAL_NUDGE_MESSAGE
from src.compile.middleware.terminal_decision_guard import TerminalDecisionGuardMiddleware

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
    "TerminalDecisionGuardMiddleware",
]

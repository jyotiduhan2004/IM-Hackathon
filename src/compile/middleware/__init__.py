"""Agent middleware for the compile pipeline.

Each middleware is additive — append to the list on `create_deep_agent`
rather than replacing defaults. See `compiler.create_compiler` for wiring.
"""

from src.compile.middleware.check_my_work_gate import GATE_REJECT_MESSAGE
from src.compile.middleware.check_my_work_gate import GATE_REJECT_PAT
from src.compile.middleware.check_my_work_gate import CheckMyWorkGateMiddleware
from src.compile.middleware.chronological_scope import ChronologicalScopeMiddleware
from src.compile.middleware.entity_write_autoheal import EntityWriteAutohealMiddleware
from src.compile.middleware.legacy_page_hint import LegacyPageHintMiddleware
from src.compile.middleware.path_autoheal import PathAutohealMiddleware
from src.compile.middleware.same_thread_topic_guard import SameThreadTopicGuardMiddleware

__all__ = [
    "GATE_REJECT_MESSAGE",
    "GATE_REJECT_PAT",
    "CheckMyWorkGateMiddleware",
    "ChronologicalScopeMiddleware",
    "EntityWriteAutohealMiddleware",
    "LegacyPageHintMiddleware",
    "PathAutohealMiddleware",
    "SameThreadTopicGuardMiddleware",
]

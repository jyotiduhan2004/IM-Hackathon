"""Thread-grouping helper for the compile coordinator."""

from __future__ import annotations

from collections import defaultdict


def _group_by_thread(
    emails: list[dict[str, str]], max_per_group: int
) -> list[list[dict[str, str]]]:
    """Group emails by thread_id, chronological within thread, threads ordered
    by earliest message date.

    Threads longer than `max_per_group` are split into sub-groups (still in
    order). Emails without a thread_id become singleton groups. The whole
    return list is sorted by each group's earliest date, so callers can
    process batches in chronological order across threads.
    """
    by_thread: dict[str, list[dict[str, str]]] = defaultdict(list)
    standalone: list[list[dict[str, str]]] = []

    for email in emails:
        tid = email.get("thread_id") or ""
        if tid:
            by_thread[tid].append(email)
        else:
            standalone.append([email])

    groups: list[list[dict[str, str]]] = []
    for members in by_thread.values():
        members.sort(key=lambda e: e.get("date", ""))
        # Split huge threads for safety; most will be one group
        for i in range(0, len(members), max_per_group):
            groups.append(members[i : i + max_per_group])
    groups.extend(standalone)

    # Threads processed in order of their earliest message — strict
    # chronological for supersession detection across topics.
    groups.sort(key=lambda g: min(e.get("date", "") for e in g) if g else "")
    return groups

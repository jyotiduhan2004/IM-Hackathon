"""Tests for deterministic entity-slug generation."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.wiki import entities as entities_module
from src.wiki.entities import create_entity_page
from src.wiki.entities import create_entity_pages
from src.wiki.entities import email_to_slug
from src.wiki.entities import find_entity_by_email
from src.wiki.entities import is_external_email
from src.wiki.entities import is_valid_email


# By default, assume emails have NO participants catalog entries — that's
# the behaviour tests in TestCreateEntityPage were written against before
# the evidence gate landed. Individual tests in TestCreateEntityEvidenceGate
# override this with richer counts. Autouse so we don't have to thread a
# fixture through every existing test.
@pytest.fixture(autouse=True)
def _default_strong_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend every email is in From of one message by default.

    Previous tests asserted "any call to create_entity_page creates a
    page". The new evidence gate would break that assumption when the DB
    is unreachable (everyone gets weak). Stub the lookup to return strong
    evidence so the legacy happy-path tests still pass; evidence-gate
    tests below override this.
    """
    monkeypatch.setattr(
        entities_module,
        "_evidence_counts",
        lambda _email: {
            "from_count": 1,
            "to_count": 0,
            "cc_count": 0,
            "distinct_threads": 1,
        },
    )


class TestEmailToSlug:
    def test_basic_internal(self) -> None:
        assert email_to_slug("amit@indiamart.com") == "amit-indiamart-com"

    def test_dotted_local_part(self) -> None:
        assert email_to_slug("amit.patel@indiamart.com") == "amit-patel-indiamart-com"

    def test_digits_in_local_part(self) -> None:
        assert email_to_slug("akash.singh6@indiamart.com") == "akash-singh6-indiamart-com"
        assert email_to_slug("vishakha.01@indiamart.com") == "vishakha-01-indiamart-com"

    def test_plus_and_hyphen(self) -> None:
        assert email_to_slug("first.last+tag@gmail.com") == "first-last-tag-gmail-com"
        assert email_to_slug("jean-paul@example.co") == "jean-paul-example-co"

    def test_case_insensitive(self) -> None:
        assert email_to_slug("Amit.Patel@IndiaMART.com") == "amit-patel-indiamart-com"

    def test_deterministic(self) -> None:
        """Same email in, same slug out. Always."""
        for _ in range(5):
            assert email_to_slug("ruchi.gupta1@indiamart.com") == ("ruchi-gupta1-indiamart-com")

    def test_strips_whitespace(self) -> None:
        assert email_to_slug("  amit@indiamart.com  ") == "amit-indiamart-com"

    def test_missing_at(self) -> None:
        with pytest.raises(ValueError, match="not an email"):
            email_to_slug("notanemail")

    def test_empty_local(self) -> None:
        with pytest.raises(ValueError, match="slug would be empty"):
            email_to_slug("@indiamart.com")

    def test_empty_domain(self) -> None:
        with pytest.raises(ValueError, match="slug would be empty"):
            email_to_slug("amit@")

    def test_type_error_on_non_str(self) -> None:
        with pytest.raises(TypeError):
            email_to_slug(123)  # type: ignore[arg-type]


class TestIsValidEmail:
    @pytest.mark.parametrize(
        "email",
        [
            "amit@indiamart.com",
            "first.last@example.co.uk",
            "user+tag@example.com",
            "jean-paul@example.co",
        ],
    )
    def test_valid(self, email: str) -> None:
        assert is_valid_email(email)

    @pytest.mark.parametrize(
        "email",
        [
            "notanemail",
            "@indiamart.com",
            "amit@",
            "amit@@indiamart.com",
            "amit space@indiamart.com",
        ],
    )
    def test_invalid(self, email: str) -> None:
        assert not is_valid_email(email)


class TestCreateEntityPage:
    def test_creates_stub_for_new_email(self, tmp_path: Path) -> None:
        result = create_entity_page(
            "amit@indiamart.com",
            display_name="Amit Jain",
            entities_dir=tmp_path,
        )
        assert result["ok"] is True
        assert result["slug"] == "amit-indiamart-com"
        assert result["created"] is True
        assert result["email"] == "amit@indiamart.com"
        path = Path(result["path"])
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "title: Amit Jain" in content
        assert "page_type: person" in content
        assert "status: active" in content
        assert "email: amit@indiamart.com" in content
        assert "is_external: false" in content
        assert "Email: amit@indiamart.com" in content

    def test_stub_marks_external_email(self, tmp_path: Path) -> None:
        result = create_entity_page(
            "john@gmail.com", display_name="John Doe", entities_dir=tmp_path
        )
        assert result["slug"] == "john-gmail-com"
        content = Path(result["path"]).read_text(encoding="utf-8")
        assert "is_external: true" in content

    def test_idempotent_same_email_same_slug(self, tmp_path: Path) -> None:
        first = create_entity_page(
            "amit@indiamart.com", display_name="Amit Jain", entities_dir=tmp_path
        )
        second = create_entity_page(
            "amit@indiamart.com",
            display_name="Different Name",
            entities_dir=tmp_path,
        )
        assert first["slug"] == second["slug"]
        assert first["path"] == second["path"]
        assert first["created"] is True
        assert second["created"] is False
        # Second call did NOT overwrite the stub's title
        content = Path(first["path"]).read_text(encoding="utf-8")
        assert "title: Amit Jain" in content
        assert "title: Different Name" not in content

    def test_finds_legacy_displayname_slug(self, tmp_path: Path) -> None:
        """Pre-existing page with display-name slug + `email:` frontmatter
        must be returned unchanged — no new stub at the email-slug path."""
        legacy = tmp_path / "amit-agarwal.md"
        legacy.write_text(
            "---\n"
            "title: Amit Agarwal\n"
            "page_type: entity\n"
            "status: current\n"
            "email: amit@indiamart.com\n"
            "---\n\n"
            "Email: amit@indiamart.com\n\n"
            "Founder of IndiaMART.\n",
            encoding="utf-8",
        )

        result = create_entity_page(
            "amit@indiamart.com",
            display_name="Amit Jain",  # intentionally different
            entities_dir=tmp_path,
        )
        assert result["ok"] is True
        assert result["slug"] == "amit-agarwal"
        assert result["path"] == str(legacy)
        assert result["created"] is False
        # No new amit-indiamart-com.md was created
        assert not (tmp_path / "amit-indiamart-com.md").exists()

    def test_case_insensitive_email_match(self, tmp_path: Path) -> None:
        result = create_entity_page("Amit@IndiaMART.com", entities_dir=tmp_path)
        assert result["email"] == "amit@indiamart.com"
        assert result["slug"] == "amit-indiamart-com"

    def test_invalid_email_returns_error(self, tmp_path: Path) -> None:
        result = create_entity_page("not-an-email", entities_dir=tmp_path)
        assert result["ok"] is False
        assert "invalid email" in result["error"]

    def test_empty_email_returns_error(self, tmp_path: Path) -> None:
        result = create_entity_page("", entities_dir=tmp_path)
        assert result["ok"] is False
        assert "required" in result["error"]

    def test_no_display_name_uses_email_as_title(self, tmp_path: Path) -> None:
        result = create_entity_page("akash.singh6@indiamart.com", entities_dir=tmp_path)
        assert result["created"] is True
        content = Path(result["path"]).read_text(encoding="utf-8")
        assert "title: akash.singh6@indiamart.com" in content

    def test_creates_entities_dir_if_missing(self, tmp_path: Path) -> None:
        entities_dir = tmp_path / "entities"  # does not exist yet
        result = create_entity_page(
            "ruchi.gupta1@indiamart.com",
            display_name="Ruchi Gupta",
            entities_dir=entities_dir,
        )
        assert result["ok"] is True
        assert entities_dir.is_dir()
        assert Path(result["path"]).exists()


class TestIsExternalEmail:
    @pytest.mark.parametrize(
        "email",
        [
            "amit@indiamart.com",
            "first.last@indiamart.com",
            "Amit@IndiaMART.com",  # case-insensitive
        ],
    )
    def test_internal(self, email: str) -> None:
        assert is_external_email(email) is False

    @pytest.mark.parametrize(
        "email",
        [
            "john@gmail.com",
            "user@example.co.uk",
            "x@indiamart.in",  # different TLD is NOT internal
            "notanemail",  # no @ treated as external
        ],
    )
    def test_external(self, email: str) -> None:
        assert is_external_email(email) is True


class TestFindEntityByEmail:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_entity_by_email("ghost@nowhere.com", entities_dir=tmp_path) is None

    def test_matches_email_in_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "foo.md"
        p.write_text(
            "---\nemail: foo@bar.com\npage_type: entity\n---\n\nbody\n",
            encoding="utf-8",
        )
        assert find_entity_by_email("foo@bar.com", entities_dir=tmp_path) == p

    def test_case_insensitive(self, tmp_path: Path) -> None:
        p = tmp_path / "foo.md"
        p.write_text(
            "---\nemail: Foo@Bar.com\npage_type: entity\n---\n\nbody\n",
            encoding="utf-8",
        )
        assert find_entity_by_email("foo@bar.com", entities_dir=tmp_path) == p

    def test_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        absent = tmp_path / "does-not-exist"
        assert find_entity_by_email("x@y.com", entities_dir=absent) is None

    def test_dual_scan_prefers_people_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production path (entities_dir=None): hit in `wiki/people/` wins."""
        (tmp_path / "people").mkdir()
        (tmp_path / "entities").mkdir()
        person_page = tmp_path / "people" / "new-slug.md"
        person_page.write_text("---\nemail: a@b.com\npage_type: person\n---\n", encoding="utf-8")
        (tmp_path / "entities" / "legacy-slug.md").write_text(
            "---\nemail: a@b.com\npage_type: entity\n---\n", encoding="utf-8"
        )
        monkeypatch.setattr(entities_module.settings, "wiki_dir", tmp_path)
        assert find_entity_by_email("a@b.com") == person_page

    def test_dual_scan_falls_back_to_legacy_entities_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production path (entities_dir=None): legacy `wiki/entities/`
        stubs still resolve when nothing in `wiki/people/` matches. This
        is the shim that lets the compiler reach un-migrated stragglers
        until C1 is fully rolled out.
        """
        (tmp_path / "people").mkdir()
        (tmp_path / "entities").mkdir()
        legacy = tmp_path / "entities" / "old-slug.md"
        legacy.write_text(
            "---\nemail: legacy@indiamart.com\npage_type: entity\n---\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(entities_module.settings, "wiki_dir", tmp_path)
        assert find_entity_by_email("legacy@indiamart.com") == legacy


def _patch_evidence(monkeypatch: pytest.MonkeyPatch, counts: dict[str, int]) -> None:
    """Pin `_evidence_counts` to a fixed dict for the current test."""
    monkeypatch.setattr(entities_module, "_evidence_counts", lambda _e: counts)


class TestCreateEntityEvidenceGate:
    """Verify the evidence gate added to stop the compiler producing 1-line
    CC-only entity stubs (see docs/BACKLOG.md). The gate sits in front of
    new-page creation only; existing-page lookups are unaffected.
    """

    def test_weak_evidence_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # CC-only, single thread → weak → refuse.
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 0, "cc_count": 1, "distinct_threads": 1},
        )
        result = create_entity_page(
            "manay.shankar@indiamart.com",
            display_name="Manay Shankar",
            entities_dir=tmp_path,
        )
        assert result["ok"] is False
        assert result["reason"] == "weak_evidence"
        assert result["email"] == "manay.shankar@indiamart.com"
        assert result["would_be_slug"] == "manay-shankar-indiamart-com"
        assert result["evidence_summary"] == {
            "from_count": 0,
            "to_count": 0,
            "cc_count": 1,
            "distinct_threads": 1,
        }
        assert "force=True" in result["guidance"]
        # No file was written.
        assert not (tmp_path / "manay-shankar-indiamart-com.md").exists()


class TestCreateEntityPagesBatch:
    def test_creates_multiple_entities_when_emails_are_in_raw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_evidence(
            monkeypatch,
            {"from_count": 1, "to_count": 0, "cc_count": 0, "distinct_threads": 1},
        )
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "thread-a.md").write_text(
            "From: owner@indiamart.com\nTo: amit@indiamart.com\nCC: ruchi@indiamart.com\n",
            encoding="utf-8",
        )

        result = create_entity_pages(
            ["raw/thread-a.md"],
            [
                {"email": "amit@indiamart.com", "display_name": "Amit"},
                {"email": "ruchi@indiamart.com", "display_name": "Ruchi"},
            ],
            entities_dir=tmp_path / "wiki" / "entities",
            raw_dir=raw_dir,
        )

        assert result["ok"] is True
        assert result["validated_raw_paths"] == ["raw/thread-a.md"]
        assert [item["email"] for item in result["results"]] == [
            "amit@indiamart.com",
            "ruchi@indiamart.com",
        ]
        assert all(item["created"] is True for item in result["results"])
        assert all(item["matched_raw_paths"] == ["raw/thread-a.md"] for item in result["results"])

    def test_rejects_email_not_present_in_raw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_evidence(
            monkeypatch,
            {"from_count": 1, "to_count": 0, "cc_count": 0, "distinct_threads": 1},
        )
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "thread-a.md").write_text("From: owner@indiamart.com\n", encoding="utf-8")

        result = create_entity_pages(
            ["raw/thread-a.md"],
            [{"email": "ghost@indiamart.com", "display_name": "Ghost"}],
            entities_dir=tmp_path / "wiki" / "entities",
            raw_dir=raw_dir,
        )

        assert result["ok"] is False
        assert result["results"][0]["reason"] == "email_not_in_raw"
        assert result["results"][0]["email"] == "ghost@indiamart.com"
        assert not (tmp_path / "wiki" / "entities" / "ghost-indiamart-com.md").exists()

    def test_weak_evidence_with_force_creates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same weak-evidence email, but the agent has decided it's
        # writing substantive content — force=True bypasses the gate.
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 0, "cc_count": 1, "distinct_threads": 1},
        )
        result = create_entity_page(
            "manay.shankar@indiamart.com",
            display_name="Manay Shankar",
            entities_dir=tmp_path,
            force=True,
        )
        assert result["ok"] is True
        assert result["created"] is True
        assert result["slug"] == "manay-shankar-indiamart-com"
        assert result["evidence_level"] == "forced"
        assert Path(result["path"]).exists()

    def test_strong_evidence_creates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_evidence(
            monkeypatch,
            {"from_count": 3, "to_count": 1, "cc_count": 0, "distinct_threads": 4},
        )
        result = create_entity_page(
            "rajeev@indiamart.com", display_name="Rajeev", entities_dir=tmp_path
        )
        assert result["ok"] is True
        assert result["created"] is True
        assert result["evidence_level"] == "strong"
        assert Path(result["path"]).exists()

    def test_strong_via_to_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Strong = `from_count > 0 OR to_count > 0`. Cover the to-only branch."""
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 1, "cc_count": 0, "distinct_threads": 1},
        )
        result = create_entity_page(
            "to-only@indiamart.com", display_name="To Only", entities_dir=tmp_path
        )
        assert result["ok"] is True
        assert result["evidence_level"] == "strong"

    def test_db_failure_falls_through_to_zero_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """psycopg.Error degrades to zero counts instead of raising.

        Re-import the module so the autouse fixture's stub of
        `_evidence_counts` is not in the way — we want to exercise the
        real function's narrow except clause.
        """
        import importlib

        import psycopg
        from src.wiki import entities as entities_module

        def boom(_email: str) -> dict[str, int]:
            raise psycopg.OperationalError("connection lost")

        monkeypatch.setattr("src.db.participants.count_appearances_by_role", boom, raising=False)
        fresh = importlib.reload(entities_module)
        counts = fresh._evidence_counts("anyone@nowhere.com")
        assert counts == {
            "from_count": 0,
            "to_count": 0,
            "cc_count": 0,
            "distinct_threads": 0,
        }

    def test_existing_page_bypasses_evidence_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Seed an existing page (canonical slug path).
        canonical = tmp_path / "ghost-nowhere-com.md"
        canonical.write_text(
            "---\n"
            "title: Ghost\n"
            "page_type: entity\n"
            "status: current\n"
            "email: ghost@nowhere.com\n"
            "---\n\n"
            "Email: ghost@nowhere.com\n",
            encoding="utf-8",
        )
        # Force the evidence check to "weak". It must NOT fire for the
        # existing page — recompiling a weak-signal page we already have
        # should be a no-op, not a regression.
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 0, "cc_count": 0, "distinct_threads": 0},
        )
        result = create_entity_page(
            "ghost@nowhere.com", display_name="Ghost", entities_dir=tmp_path
        )
        assert result["ok"] is True
        assert result["created"] is False
        assert result["slug"] == "ghost-nowhere-com"
        assert result["path"] == str(canonical)

    def test_existing_legacy_page_bypasses_evidence_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pre-existing display-name slug like we have lots of in
        # production wiki/entities/. `find_entity_by_email` resolves via
        # frontmatter and must still return it even under weak counts.
        legacy = tmp_path / "some-body.md"
        legacy.write_text(
            "---\nemail: some.body@indiamart.com\npage_type: entity\n---\n",
            encoding="utf-8",
        )
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 0, "cc_count": 0, "distinct_threads": 0},
        )
        result = create_entity_page("some.body@indiamart.com", entities_dir=tmp_path)
        assert result["ok"] is True
        assert result["slug"] == "some-body"
        assert result["created"] is False

    def test_medium_evidence_creates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Not in From/To anywhere, but CC'd across 3 distinct threads →
        # medium. The person is recurring; a page is warranted.
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 0, "cc_count": 4, "distinct_threads": 3},
        )
        result = create_entity_page(
            "recurring.cc@indiamart.com",
            display_name="Recurring CC",
            entities_dir=tmp_path,
        )
        assert result["ok"] is True
        assert result["created"] is True
        assert result["evidence_level"] == "medium"
        assert Path(result["path"]).exists()

    def test_weak_cc_single_thread_refuses_even_with_many_ccs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Lots of CC rows but all on ONE thread (e.g. one long reply
        # chain) is still weak — we want thread diversity to be the
        # signal, not raw CC volume.
        _patch_evidence(
            monkeypatch,
            {"from_count": 0, "to_count": 0, "cc_count": 8, "distinct_threads": 1},
        )
        result = create_entity_page(
            "pramod.purohit@indiamart.com",
            display_name="Pramod Purohit",
            entities_dir=tmp_path,
        )
        assert result["ok"] is False
        assert result["reason"] == "weak_evidence"

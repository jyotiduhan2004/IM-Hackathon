"""Tests for deterministic entity-slug generation."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.compile.entities import create_entity_page
from src.compile.entities import email_to_slug
from src.compile.entities import find_entity_by_email
from src.compile.entities import is_external_email
from src.compile.entities import is_valid_email


class TestEmailToSlug:
    def test_basic_internal(self) -> None:
        assert email_to_slug("amit@indiamart.com") == "amit-indiamart-com"

    def test_dotted_local_part(self) -> None:
        assert (
            email_to_slug("amit.patel@indiamart.com") == "amit-patel-indiamart-com"
        )

    def test_digits_in_local_part(self) -> None:
        assert (
            email_to_slug("akash.singh6@indiamart.com")
            == "akash-singh6-indiamart-com"
        )
        assert (
            email_to_slug("vishakha.01@indiamart.com")
            == "vishakha-01-indiamart-com"
        )

    def test_plus_and_hyphen(self) -> None:
        assert (
            email_to_slug("first.last+tag@gmail.com") == "first-last-tag-gmail-com"
        )
        assert (
            email_to_slug("jean-paul@example.co") == "jean-paul-example-co"
        )

    def test_case_insensitive(self) -> None:
        assert (
            email_to_slug("Amit.Patel@IndiaMART.com") == "amit-patel-indiamart-com"
        )

    def test_deterministic(self) -> None:
        """Same email in, same slug out. Always."""
        for _ in range(5):
            assert email_to_slug("ruchi.gupta1@indiamart.com") == (
                "ruchi-gupta1-indiamart-com"
            )

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
        assert "page_type: entity" in content
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
        result = create_entity_page(
            "Amit@IndiaMART.com", entities_dir=tmp_path
        )
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
        result = create_entity_page(
            "akash.singh6@indiamart.com", entities_dir=tmp_path
        )
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

"""Licensed linked-template imports, including security and project fidelity."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sixsentences_server.api.app as api_app
from sixsentences_server.acquisition.models import FetchedBlob
from sixsentences_server.api.app import create_app
from sixsentences_server.core.db import Org, db_session
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.writer.project_import import import_latex_project
from sixsentences_server.writer.template_import import (
    RemoteTemplatePackage,
    TemplateImportError,
    inspect_remote_template,
)


def _authed(app: FastAPI, email: str = "owner@example.org") -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": email, "name": "Owner"},
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _grant_scout(client: TestClient) -> None:
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"


def _project_zip(*, dangerous: bool = False, licensed: bool = True) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        main = (
            "\\documentclass{vgtc}\n\\begin{document}\n"
            + ("\\immediate\\write18{curl bad.example}" if dangerous else "Research")
            + "\n\\end{document}"
        )
        archive.writestr("template-main/template.tex", main)
        archive.writestr("template-main/vgtc.cls", "% class")
        archive.writestr("template-main/references.bib", "@article{x,title={X}}")
        archive.writestr("template-main/journal.bst", "ENTRY {} {} {}")
        archive.writestr("template-main/figs/diagram.png", b"PNG")
        if licensed:
            archive.writestr(
                "template-main/LICENSE",
                "Creative Commons Attribution 4.0 International (CC BY 4.0)",
            )
    return output.getvalue()


def _blob(content: bytes, content_type: str, url: str) -> FetchedBlob:
    return FetchedBlob(content=content, content_type=content_type, final_url=url)


def test_overleaf_gallery_import_uses_official_project_and_license() -> None:
    overleaf = "https://www.overleaf.com/latex/templates/ieee/gpptdvctxtgb"
    github = "https://github.com/ieeevgtc/tvcg-journal-latex"
    archive_url = f"{github}/archive/refs/heads/main.zip"
    page = b'\n        <html><h1>IEEE TVCG Journal Style Template</h1>\n        <p>Author: IEEE VGTC Last Updated: today\n        License: Creative Commons CC BY 4.0 Abstract: Official template</p>\n        <a href="https://github.com/ieeevgtc/tvcg-journal-latex">source</a></html>\n    '
    responses = {
        overleaf: _blob(page, "text/html", overleaf),
        github: _blob(b'{"defaultBranch":"main"}', "text/html", github),
        archive_url: _blob(_project_zip(), "application/zip", archive_url),
    }
    package = inspect_remote_template(overleaf, fetch_public=lambda url, _limit: responses.get(url))
    assert package.provider == "Overleaf"
    assert package.title == "IEEE TVCG Journal Style Template"
    assert package.author == "IEEE VGTC"
    assert package.license_name == "Creative Commons CC BY 4.0"
    assert package.license_status == "open"
    assert package.rights_confirmation_required is False
    assert package.upstream_url == github
    assert {path for path, _content in package.extra_files} >= {
        "vgtc.cls",
        "references.bib",
        "journal.bst",
    }
    assert [path for path, _content in package.assets] == ["figs/diagram.png"]


def test_direct_unknown_license_requires_confirmation() -> None:
    url = "https://publisher.example/template.zip"
    package = inspect_remote_template(
        url,
        fetch_public=lambda _url, _limit: _blob(
            _project_zip(licensed=False), "application/zip", url
        ),
    )
    assert package.license_status == "unverified"
    assert package.license_name == "License not stated"
    assert package.rights_confirmation_required is True


def test_publisher_page_finds_public_project_download() -> None:
    page_url = "https://publisher.example/templates/article"
    zip_url = "https://publisher.example/files/article.zip"
    responses = {
        page_url: _blob(
            b'<h1>Official article</h1><a href="/files/article.zip">Download ZIP</a>',
            "text/html",
            page_url,
        ),
        zip_url: _blob(_project_zip(), "application/zip", zip_url),
    }
    package = inspect_remote_template(page_url, fetch_public=lambda url, _limit: responses.get(url))
    assert package.provider == "Publisher"
    assert package.title == "Official article"
    assert package.upstream_url == zip_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/template.zip",
        "https://user:secret@example.org/template.zip",
        "file:///tmp/template.zip",
        "https://",
    ],
)
def test_template_link_rejects_non_public_shapes(url: str) -> None:
    with pytest.raises(TemplateImportError, match="public HTTPS"):
        inspect_remote_template(url, fetch_public=lambda _url, _limit: None)


def test_remote_template_blocks_shell_escape() -> None:
    url = "https://publisher.example/template.zip"
    with pytest.raises(TemplateImportError, match="\\\\write18"):
        inspect_remote_template(
            url,
            fetch_public=lambda _url, _limit: _blob(
                _project_zip(dangerous=True), "application/zip", url
            ),
        )


def test_project_import_keeps_bibliography_styles_and_eps_assets() -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("paper/main.tex", "\\documentclass{article}\\begin{document}x")
        archive.writestr("paper/citation.bst", "ENTRY {} {} {}")
        archive.writestr("paper/figure.eps", b"%!PS")
    _title, _main, files, assets = import_latex_project(output.getvalue(), "paper.zip")
    assert files == [("citation.bst", "ENTRY {} {} {}")]
    assert assets == [("figure.eps", b"%!PS")]


def test_project_import_accepts_latex_entry_inside_zip() -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "paper/manuscript.latex",
            "\\documentclass{article}\\begin{document}Research\\end{document}",
        )
        archive.writestr("paper/references.bib", "@article{x,title={X}}")
    _title, main, files, _assets = import_latex_project(output.getvalue(), "paper.zip")
    assert main.startswith("\\documentclass{article}")
    assert files == [("references.bib", "@article{x,title={X}}")]


def test_remote_template_checks_bibliography_style_sources() -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "paper/main.tex", "\\documentclass{article}\\begin{document}Research\\end{document}"
        )
        archive.writestr("paper/citation.bbx", "\\immediate\\write18{curl bad.example}")
    url = "https://publisher.example/template.zip"
    with pytest.raises(TemplateImportError, match="\\\\write18"):
        inspect_remote_template(
            url, fetch_public=lambda _url, _limit: _blob(output.getvalue(), "application/zip", url)
        )


def test_linked_template_api_preserves_provenance_and_full_project(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = RemoteTemplatePackage(
        source_url="https://www.overleaf.com/latex/templates/example/token",
        upstream_url="https://github.com/example/template",
        provider="Overleaf",
        title="Conference template",
        author="Example Society",
        license_name="Creative Commons CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        license_status="open",
        package_filename="template.zip",
        package=_project_zip(),
        main_content="\\documentclass{vgtc}\\begin{document}Research\\end{document}",
        extra_files=(
            ("vgtc.cls", "% class"),
            ("references.bib", "@article{x,title={X}}"),
            ("journal.bst", "ENTRY {} {} {}"),
            ("LICENSE", "Creative Commons Attribution 4.0 International (CC BY 4.0)"),
        ),
        assets=(("figs/diagram.png", b"PNG"),),
        warnings=("Review venue instructions.",),
    )
    monkeypatch.setattr(api_app, "inspect_remote_template", lambda _url: package)
    client = _authed(create_app())
    preview = client.post("/writer/templates/link/preview", json={"url": package.source_url})
    assert preview.status_code == 200
    assert preview.json()["file_count"] == 6
    imported = client.post(
        "/writer/templates/link", json={"url": package.source_url, "rights_confirmed": False}
    )
    assert imported.status_code == 201, imported.text
    template = imported.json()
    assert template["origin"]["provider"] == "Overleaf"
    assert template["origin"]["license_status"] == "open"
    assert template["origin"]["sha256"] == package.sha256
    duplicate = client.post(
        "/writer/templates/link", json={"url": package.source_url, "rights_confirmed": False}
    )
    assert duplicate.status_code == 409
    document = client.post("/writer", json={"title": "From link", "template_id": template["id"]})
    assert document.status_code == 201, document.text
    doc = document.json()
    files = client.get(f"/writer/{doc['public_id']}/files").json()
    assert {row["path"] for row in files} == {
        "main.tex",
        "LICENSE",
        "vgtc.cls",
        "references.bib",
        "journal.bst",
    }
    assets = client.get(f"/writer/{doc['public_id']}/assets").json()
    assert [asset["filename"] for asset in assets] == ["figs/diagram.png"]
    monkeypatch.setattr(api_app, "enqueue_job", lambda *args, **kwargs: None)
    preview_retarget = client.post(
        f"/writer/{doc['public_id']}/retarget/preview",
        json={"template_id": template["id"], "expected_revision": doc["revision"]},
    )
    assert preview_retarget.status_code == 200, preview_retarget.text
    assert preview_retarget.json()["report"]["template_files"] == 4
    assert preview_retarget.json()["report"]["template_assets"] == 1
    retargeted = client.post(
        f"/writer/{doc['public_id']}/retarget",
        json={
            "template_id": template["id"],
            "title": "Retargeted from link",
            "expected_revision": doc["revision"],
        },
    )
    assert retargeted.status_code == 201, retargeted.text
    target_files = client.get(f"/writer/{retargeted.json()['public_id']}/files").json()
    assert {row["path"] for row in target_files} >= {
        "main.tex",
        "LICENSE",
        "vgtc.cls",
        "references.bib",
        "journal.bst",
    }
    target_assets = client.get(f"/writer/{retargeted.json()['public_id']}/assets").json()
    assert [asset["filename"] for asset in target_assets] == ["figs/diagram.png"]
    other = _authed(create_app(), "other@example.org")
    _grant_scout(other)
    assert other.get("/writer/templates").json() == []
    assert client.delete(f"/writer/templates/{template['id']}").status_code == 200
    assert client.get("/writer/templates").json() == []


def test_unknown_license_api_requires_explicit_rights(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = RemoteTemplatePackage(
        source_url="https://publisher.example/template.zip",
        upstream_url="https://publisher.example/template.zip",
        provider="Direct",
        title="Private template",
        author="",
        license_name="License not stated",
        license_url="",
        license_status="unverified",
        package_filename="template.zip",
        package=_project_zip(licensed=False),
        main_content="\\documentclass{article}",
        extra_files=(),
        assets=(),
        warnings=(),
    )
    monkeypatch.setattr(api_app, "inspect_remote_template", lambda _url: package)
    client = _authed(create_app())
    _grant_scout(client)
    refused = client.post(
        "/writer/templates/link", json={"url": package.source_url, "rights_confirmed": False}
    )
    assert refused.status_code == 422
    imported = client.post(
        "/writer/templates/link", json={"url": package.source_url, "rights_confirmed": True}
    )
    assert imported.status_code == 201
    assert imported.json()["origin"]["license_status"] == "confirmed"
    assert imported.json()["origin"]["rights_confirmed"] is True

"""API level tests (PRD chapter 13.4)."""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

# The database and the data directory are set by tests/conftest.py before
# anything of the application is imported - the tests never touch the
# deployment.
from app.main import app  # noqa: E402
from app.db import init_db  # noqa: E402

init_db()
client = TestClient(app)


@pytest.fixture(scope="module")
def files():
    from pathlib import Path

    from tests.fixtures import build_docx, build_pdf, build_xlsx

    tmp = Path(tempfile.mkdtemp())
    docx_a = build_docx(tmp / "a.docx", ["合同金额为100万元", "第二条内容"])
    docx_b = build_docx(tmp / "b.docx", ["合同金额为120万元", "第二条内容"])
    xlsx_a = build_xlsx(tmp / "a.xlsx", {"Sheet1": [["编号", "金额"], ["001", 100]]})
    xlsx_b = build_xlsx(tmp / "b.xlsx", {"Sheet1": [["编号", "金额"], ["001", 200]]})
    pdf_a = build_pdf(tmp / "a.pdf", [["合同金额为100万元", "第二条内容"]])
    return {
        "docx_a": docx_a,
        "docx_b": docx_b,
        "xlsx_a": xlsx_a,
        "xlsx_b": xlsx_b,
        "pdf_a": pdf_a,
    }


def upload(path, filename):
    with open(path, "rb") as handle:
        response = client.post(
            "/api/files/upload", files={"file": (filename, handle.read())}
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello")
    with open(path, "rb") as handle:
        response = client.post(
            "/api/files/upload", files={"file": ("a.txt", handle.read())}
        )
    assert response.status_code == 400


def test_upload_rejects_mismatched_content(pdf_factory):
    path = pdf_factory("a.docx", [["content"]])
    with open(path, "rb") as handle:
        response = client.post(
            "/api/files/upload", files={"file": ("a.docx", handle.read())}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["detected_type"] == "pdf"
    assert body["format_mismatch"] is True


def test_document_comparison_flow(files):
    file_a = upload(files["docx_a"], "a.docx")
    file_b = upload(files["docx_b"], "b.docx")

    response = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["status"] == "completed"
    assert summary["mode"] == "document_structure"
    assert summary["stats"]["modified"] == 1

    comparison_id = summary["comparison_id"]

    result = client.get(f"/api/comparisons/{comparison_id}/result").json()
    assert len(result["changes"]) == 1
    assert result["changes"][0]["primary_type"] == "modified"

    changes = client.get(f"/api/comparisons/{comparison_id}/changes").json()
    assert len(changes) == 1

    filtered = client.get(
        f"/api/comparisons/{comparison_id}/changes", params={"change_type": "added"}
    ).json()
    assert filtered == []

    detail = client.get(
        f"/api/comparisons/{comparison_id}/changes/{result['changes'][0]['change_id']}"
    ).json()
    assert detail["primary_type"] == "modified"

    preview = client.get(f"/api/files/{file_a['file_id']}/preview").json()
    assert preview["blocks"]

    export = client.post(
        f"/api/comparisons/{comparison_id}/export", params={"format": "markdown"}
    )
    assert export.status_code == 200
    assert "文档比对差异报告" in export.text


def test_document_render_serves_docx(files):
    """原貌预览：DOCX 上传直接返回 DOCX 容器（docx-preview 渲染）。"""
    file_a = upload(files["docx_a"], "a.docx")
    response = client.get(f"/api/files/{file_a['file_id']}/render")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # A DOCX is a zip container.
    assert response.content[:2] == b"PK"


def test_document_render_converts_legacy_doc(docx_factory):
    """DOC / WPS 先由 LibreOffice 转成 DOCX，再缓存供前端渲染。"""
    from app.parsers import libreoffice

    if not libreoffice.is_available():
        pytest.skip("需要安装 LibreOffice 才能转换 DOC")

    # The fixture builds a Word 97 container that LibreOffice accepts; the
    # minimal hand-built .doc used by the parser tests is not a valid
    # intermediate product for the converter.
    source = docx_factory("legacy-source.docx", ["第一段内容", "第二段内容"])
    path = libreoffice.convert(source, "doc")
    assert path is not None, "LibreOffice 未能生成 DOC 样本"
    file_a = upload(path, "legacy.doc")
    response = client.get(f"/api/files/{file_a['file_id']}/render")
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"PK"

    # 第二次请求必须命中缓存，不再重复转换。
    cached = client.get(f"/api/files/{file_a['file_id']}/render")
    assert cached.status_code == 200
    assert cached.content == response.content


def test_table_layout_describes_merged_cells(tmp_path):
    """合并单元格的版面信息随预览一起返回。"""
    from tests.fixtures import build_docx_merged

    path = build_docx_merged(tmp_path / "merged.docx")
    file_a = upload(path, "merged.docx")
    preview = client.get(f"/api/files/{file_a['file_id']}/preview").json()

    table = next(block["table"] for block in preview["blocks"] if block["type"] == "table")
    layout = table["layout"]
    assert len(layout) == 3

    # 第一行：横向合并的单元格占两列，第三列单独一个
    assert [cell["colspan"] for cell in layout[0]] == [2, 1]
    assert layout[0][0]["text"] == "横向合并"

    # 纵向合并：单元格出现在它的第一行，后面的行只有占位
    assert layout[1][0]["rowspan"] == 2
    assert layout[1][0]["continuation"] is False
    assert layout[1][0]["text"] == "纵向合并"
    assert layout[2][0]["continuation"] is True
    assert layout[2][0]["text"] == ""

    # 网格行保持不变：比较引擎按网格列比较，合并单元格在每个网格列重复
    assert len(table["rows"][0]) == 3
    assert table["rows"][0][0] == table["rows"][0][1] == "横向合并"


def test_vertical_merge_change_is_reported_once(tmp_path):
    """纵向合并单元格只报一处差异（而不是每个跨越行各报一次）。"""
    import docx

    from tests.fixtures import build_docx_merged

    path_a = build_docx_merged(tmp_path / "merge-a.docx")
    path_b = build_docx_merged(tmp_path / "merge-b.docx")

    document = docx.Document(str(path_b))
    document.tables[0].cell(1, 0).text = "纵向修改"
    document.save(str(path_b))

    file_a = upload(path_a, "merge-a.docx")
    file_b = upload(path_b, "merge-b.docx")
    created = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    assert created.status_code == 201, created.text
    result = client.get(
        f"/api/comparisons/{created.json()['comparison_id']}/result"
    ).json()

    cell_changes = [c for c in result["changes"] if c["category"] == "table_cell"]
    assert len(cell_changes) == 1, [c["summary"] for c in cell_changes]
    # 差异落在合并单元格的起始行
    assert cell_changes[0]["document_b"]["location"]["row_index"] == 1
    assert cell_changes[0]["document_b"]["location"]["column_index"] == 0


def test_preview_exposes_pictures(tmp_path):
    """预览里能看到文档中的图片（哈希/大小），供前端标注。"""
    from tests.fixtures import PNG_RED, build_docx_with_image

    path = build_docx_with_image(tmp_path / "with-image.docx", PNG_RED)
    file_a = upload(path, "with-image.docx")
    preview = client.get(f"/api/files/{file_a['file_id']}/preview").json()

    images = [image for block in preview["blocks"] for image in block.get("images", [])]
    assert len(images) == 1
    assert images[0]["hash"]
    assert images[0]["size"] == len(PNG_RED)
    assert images[0]["type"] == "image/png"


def test_replaced_picture_is_reported(tmp_path):
    """换了图片（文字不变）应报出一处图片差异，并给出提示。"""
    from tests.fixtures import PNG_BLUE, PNG_RED, build_docx_with_image

    path_a = build_docx_with_image(tmp_path / "img-a.docx", PNG_RED)
    path_b = build_docx_with_image(tmp_path / "img-b.docx", PNG_BLUE)
    file_a = upload(path_a, "img-a.docx")
    file_b = upload(path_b, "img-b.docx")

    created = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    assert created.status_code == 201, created.text
    result = client.get(
        f"/api/comparisons/{created.json()['comparison_id']}/result"
    ).json()

    image_changes = [c for c in result["changes"] if c["category"] == "image"]
    assert len(image_changes) == 1, [c["summary"] for c in result["changes"]]
    assert image_changes[0]["primary_type"] == "modified"
    assert any("图片" in notice for notice in result["notices"])


def test_render_rejects_non_document(files):
    file_a = upload(files["xlsx_a"], "a.xlsx")
    response = client.get(f"/api/files/{file_a['file_id']}/render")
    assert response.status_code == 404


def test_spreadsheet_comparison_flow(files):
    file_a = upload(files["xlsx_a"], "a.xlsx")
    file_b = upload(files["xlsx_b"], "b.xlsx")
    response = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    summary = response.json()
    assert summary["mode"] == "spreadsheet_structure"
    assert summary["stats"]["modified"] == 1


def test_comparison_rejects_different_formats(files):
    """Only same-format pairs are accepted (产品决策：仅支持同格式比对)."""
    file_a = upload(files["docx_a"], "a.docx")
    file_b = upload(files["xlsx_b"], "b.xlsx")
    response = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    assert response.status_code == 400, response.text
    assert "同格式" in response.json()["detail"]


def test_comparison_rejects_different_formats_of_the_same_family(files):
    """DOCX and PDF are both documents, but they are not the same format."""
    file_a = upload(files["docx_a"], "a.docx")
    file_b = upload(files["pdf_a"], "a.pdf")
    response = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    assert response.status_code == 400, response.text
    assert "同格式" in response.json()["detail"]


def test_config_update_triggers_recompare(files):
    file_a = upload(files["docx_a"], "a.docx")
    file_b = upload(files["docx_b"], "b.docx")
    summary = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    ).json()
    comparison_id = summary["comparison_id"]

    config = dict(summary["config"])
    config["ignore_case"] = True
    response = client.put(
        f"/api/comparisons/{comparison_id}/config", json={"config": config}
    )
    assert response.status_code == 200


def test_comparison_not_found():
    assert client.get("/api/comparisons/does-not-exist").status_code == 404


def test_list_comparison_history(files):
    file_a = upload(files["docx_a"], "history-a.docx")
    file_b = upload(files["docx_b"], "history-b.docx")
    created = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    ).json()

    response = client.get("/api/comparisons", params={"limit": 50})
    assert response.status_code == 200, response.text
    items = response.json()
    assert items
    assert created["comparison_id"] in {item["comparison_id"] for item in items}

    item = next(i for i in items if i["comparison_id"] == created["comparison_id"])
    assert item["file_a_name"] == "history-a.docx"
    assert item["file_b_name"] == "history-b.docx"
    assert item["status"] == "completed"
    assert item["stats"]["total"] == created["stats"]["total"]

    # The stored result is reachable again from the history entry.
    detail = client.get(f"/api/comparisons/{item['comparison_id']}/result").json()
    assert detail["changes"]

    filtered = client.get(
        "/api/comparisons", params={"keyword": "history-a"}
    ).json()
    assert filtered
    assert all(
        "history-a"
        in f"{entry['file_a_name']} {entry['file_b_name']}".lower()
        for entry in filtered
    )


def test_preview_restored_from_database(files):
    """Preview must survive a process restart (PRD 14.5)."""
    from app.services.cache import drop_parse

    file_a = upload(files["docx_a"], "a.docx")
    drop_parse(file_a["file_id"])
    preview = client.get(f"/api/files/{file_a['file_id']}/preview").json()
    assert [block["text"] for block in preview["blocks"]] == [
        "合同金额为100万元",
        "第二条内容",
    ]

    file_x = upload(files["xlsx_a"], "a.xlsx")
    drop_parse(file_x["file_id"])
    preview = client.get(f"/api/files/{file_x['file_id']}/preview").json()
    assert preview["sheets"][0]["name"] == "Sheet1"
    assert preview["sheets"][0]["rows"][1] == ["001", "100"]


def test_comparison_after_cache_drop(files):
    """A comparison can be re-run from the persisted structure alone."""
    from app.services.cache import drop_parse

    file_a = upload(files["docx_a"], "a.docx")
    file_b = upload(files["docx_b"], "b.docx")
    drop_parse(file_a["file_id"])
    drop_parse(file_b["file_id"])
    summary = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    ).json()
    assert summary["status"] == "completed"
    assert summary["stats"]["modified"] == 1


def test_preview_uses_cached_result_for_formula_cells():
    """The grid must show the evaluated result, not the empty formula value."""
    from app.api.files import _preview_value
    from app.domain.models import Cell

    formula_cell = Cell(
        cell_id="c0_r1_c0",
        coordinate="A2",
        row_index=1,
        column_index=0,
        value=None,
        normalized_value=None,
        formula="=SUM(A1:A5)",
        display_value="15",
    )
    assert _preview_value(formula_cell) == "15"

    plain_cell = Cell(
        cell_id="c0_r2_c0",
        coordinate="A3",
        row_index=2,
        column_index=0,
        value="文本",
        normalized_value="文本",
    )
    assert _preview_value(plain_cell) == "文本"


def test_upload_reports_parse_progress(files):
    """The parse runs in the background and reports its progress."""
    uploaded = upload(files["docx_a"], "progress.docx")
    assert uploaded["parse_status"] in {"uploaded", "parsing", "success", "partial"}
    # ``TestClient`` waits for the background task, so the parse is done here.
    info = client.get(f"/api/files/{uploaded['file_id']}").json()
    assert info["parse_status"] == "success"
    assert info["parse_progress"] == 100
    assert info["parse_stage"] == "completed"


def test_comparison_reports_progress(files):
    """A finished task reports 100% and the final stage."""
    file_a = upload(files["docx_a"], "progress-a.docx")
    file_b = upload(files["docx_b"], "progress-b.docx")
    summary = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    ).json()
    assert summary["status"] == "completed"
    assert summary["progress"] == 100
    assert summary["stage"] == "completed"


def test_comparison_waits_for_a_lost_parse(files):
    """A file whose background parse was lost is parsed when it is needed."""
    from app.db import FileRecord, SessionLocal

    file_a = upload(files["docx_a"], "lost-a.docx")
    file_b = upload(files["docx_b"], "lost-b.docx")

    # Simulate a restart that dropped the parse task of file A.
    session = SessionLocal()
    try:
        record = session.get(FileRecord, file_a["file_id"])
        record.parse_status = "uploaded"
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"


def test_interrupted_comparison_is_recovered(files):
    """A task left "running" by a restart is executed again on startup."""
    from app.db import Comparison, SessionLocal
    from app.services.tasks import recover_interrupted_work

    file_a = upload(files["docx_a"], "recover-a.docx")
    file_b = upload(files["docx_b"], "recover-b.docx")
    summary = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    ).json()

    # The process "died" while the task was comparing.
    session = SessionLocal()
    try:
        record = session.get(Comparison, summary["comparison_id"])
        record.status = "comparing"
        record.stage = "comparing"
        record.progress = 70
        session.commit()
    finally:
        session.close()

    recover_interrupted_work()

    detail = client.get(f"/api/comparisons/{summary['comparison_id']}").json()
    assert detail["status"] == "completed"
    assert detail["progress"] == 100
    assert detail["stats"]["modified"] == 1


def test_pdf_comparison_exposes_the_raw_file_and_the_page_map(tmp_path):
    """The PDF viewer needs the original file and the page map."""
    from tests.fixtures import build_pdf_pages

    pages = ["第一页的正文内容。", "第二页的正文内容。"]
    path_a = build_pdf_pages(tmp_path / "a.pdf", [[pages[0]], [pages[1]]])
    path_b = build_pdf_pages(
        tmp_path / "b.pdf", [["封面：新增的页面。"], [pages[0]], [pages[1]]]
    )

    file_a = upload(path_a, "a.pdf")
    file_b = upload(path_b, "b.pdf")

    summary = client.post(
        "/api/comparisons",
        json={"file_a_id": file_a["file_id"], "file_b_id": file_b["file_id"]},
        params={"wait": "true"},
    ).json()
    assert summary["status"] == "completed"

    result = client.get(f"/api/comparisons/{summary['comparison_id']}/result").json()
    assert result["document_a"]["raw_url"] == f"/api/files/{file_a['file_id']}/raw"
    assert result["document_a"]["file_type"] == "pdf"
    assert result["document_a"]["page_count"] == 2
    assert result["document_b"]["page_count"] == 3
    assert result["page_mapping"]
    for pair in result["page_mapping"]:
        assert pair["left_page"] == pair["right_page"] - 1

    raw = client.get(result["document_a"]["raw_url"])
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/pdf"
    assert raw.content.startswith(b"%PDF")


def test_the_raw_file_endpoint_rejects_other_formats(files):
    """Only a PDF can be opened in the PDF viewer."""
    docx = upload(files["docx_a"], "raw-a.docx")
    assert client.get(f"/api/files/{docx['file_id']}/raw").status_code == 404

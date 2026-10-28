from __future__ import annotations

from pathlib import Path

import pytest

from dailyscraper.schema import Inputs
from dailyscraper.tasks.inputs import InputError, create_input_snapshot, load_input_snapshot


def write(path: Path, text: str, encoding: str = "utf-8-sig") -> Path:
    path.write_text(text, encoding=encoding)
    return path


def test_snapshot_keeps_valid_rows_and_reports_skipped(tmp_path: Path) -> None:
    source = write(
        tmp_path / "in.csv",
        "sku;detail_link\n1;https://a/1\n2;\n1;https://a/1\n3;https://a/3\n",
    )

    snapshot = create_input_snapshot(
        source,
        tmp_path / "run" / "inputs" / "s.csv",
        Inputs(deduplicate_by_fields=("sku",)),
    )

    assert (snapshot.rows, snapshot.skipped_invalid, snapshot.skipped_duplicates) == (2, 1, 1)
    assert [row["sku"] for row in snapshot.iter_rows()] == ["1", "3"]
    assert load_input_snapshot(snapshot.path) == snapshot


def test_snapshot_reads_the_configured_format(tmp_path: Path) -> None:
    source = write(tmp_path / "in.csv", "link,name\nhttps://a/1,Чай\n", encoding="cp1251")

    snapshot = create_input_snapshot(
        source,
        tmp_path / "s.csv",
        Inputs(encoding="cp1251", delimiter=",", required_fields=("link",)),
    )

    assert list(snapshot.iter_rows()) == [{"link": "https://a/1", "name": "Чай"}]


def test_missing_required_column_is_rejected(tmp_path: Path) -> None:
    source = write(tmp_path / "in.csv", "sku;name\n1;x\n")

    with pytest.raises(InputError, match="detail_link"):
        create_input_snapshot(source, tmp_path / "s.csv", Inputs())


def test_changed_snapshot_is_detected(tmp_path: Path) -> None:
    source = write(tmp_path / "in.csv", "detail_link\nhttps://a/1\n")
    snapshot = create_input_snapshot(source, tmp_path / "s.csv", Inputs())
    snapshot.path.write_text("detail_link\nhttps://changed\n", encoding="utf-8")

    with pytest.raises(InputError, match="does not match"):
        load_input_snapshot(snapshot.path)

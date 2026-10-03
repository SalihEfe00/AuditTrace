import datetime as dt
import json

from mufettis.corpus.currency import last_corpus_update, staleness_message


def test_last_update_is_latest_download_or_import(tmp_path):
    folder = tmp_path / "legislation_downloads"
    folder.mkdir()
    (folder / "downloads.json").write_text(json.dumps({
        "a": {"fetched_at": "2026-09-30 16:55:09"}, "b": {"fetched_at": ""}, "c": "bozuk"}), encoding="utf-8")
    (folder / "import_runs.jsonl").write_text('{"at": "2026-10-03 01:16:20"}\nbozuk satır\n{"at": "2026-09-01 00:00:00"}\n',
                                              encoding="utf-8")
    assert last_corpus_update(tmp_path) == dt.datetime(2026, 10, 3, 1, 16, 20)
    assert last_corpus_update(tmp_path / "yok") is None


def test_staleness_message():
    last = dt.datetime(2026, 10, 3)
    assert staleness_message(last, now=dt.datetime(2026, 10, 20)) is None
    msg = staleness_message(last, now=dt.datetime(2026, 11, 10))
    assert "38 gündür" in msg and "03.10.2026" in msg
    assert "bilinmiyor" in staleness_message(None)

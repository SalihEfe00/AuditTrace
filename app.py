"""AuditTrace giriş noktası: `streamlit run app.py` arayüzü açar, `python app.py --help` komutları listeler.

Bütün kod `mufettis` paketindedir; komut satırı `mufettis/cli.py`.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from mufettis.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

from pathlib import Path
import retry_failed

def test_atomic_bytes_cleans_and_preserves_crlf(tmp_path):
    p = tmp_path / "x.txt"
    retry_failed._write_bytes(p, b"a\r\nb\r\n")
    assert p.read_bytes() == b"a\r\nb\r\n"

def test_filename_issue_guard(tmp_path):
    p = tmp_path / "1期-杀数字-失败.txt"
    p.write_bytes(b"\xef\xbb\xbf")
    assert retry_failed.retry_failed_file(p, "2")[2] == 2

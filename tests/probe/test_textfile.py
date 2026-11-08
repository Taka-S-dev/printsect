from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from printsect.core.errors import PsectError
from printsect.probe.textfile import TextFile

ENC = ["utf-8", "cp932"]


def roundtrip(tmp_path: Path, data: bytes) -> TextFile:
    path = tmp_path / "a.c"
    path.write_bytes(data)
    tf = TextFile.read(path, ENC)
    tf.write()
    assert path.read_bytes() == data
    return tf


def test_untouched_files_come_back_byte_for_byte(tmp_path: Path):
    roundtrip(tmp_path, b"int a;\r\nint b;\r\n")
    roundtrip(tmp_path, b"int a;\nint b;")
    roundtrip(tmp_path, codecs.BOM_UTF8 + "// é\n".encode())
    tf = roundtrip(tmp_path, "/* 日本語 */\r\nint a;\r\n".encode("cp932"))
    assert tf.encoding == "cp932"
    assert tf.eol == "\r\n"


def test_insert_keeps_crlf_and_cp932(tmp_path: Path):
    path = tmp_path / "a.c"
    path.write_bytes("/* 日本語 */\r\nint a;\r\n".encode("cp932"))
    tf = TextFile.read(path, ENC)
    tf.insert(1, ['printf("表");'])
    tf.write()
    assert path.read_bytes() == '/* 日本語 */\r\nprintf("表");\r\nint a;\r\n'.encode("cp932")


def test_insert_and_delete_at_end_without_final_newline(tmp_path: Path):
    path = tmp_path / "a.c"
    path.write_bytes(b"int a;\nint b;")
    tf = TextFile.read(path, ENC)
    tf.insert(2, ["X;"])
    tf.write()
    assert path.read_bytes() == b"int a;\nint b;\nX;"
    tf = TextFile.read(path, ENC)
    tf.delete({2})
    tf.write()
    assert path.read_bytes() == b"int a;\nint b;"


def test_form_feed_does_not_split_lines(tmp_path: Path):
    tf = roundtrip(tmp_path, b"int a;\x0c\nint b;\n")
    assert len(tf.lines) == 2


def test_refuses_unknown_encoding(tmp_path: Path):
    path = tmp_path / "a.c"
    path.write_bytes(b"\x81\x20\n")  # a cp932 lead byte with no valid trail byte
    with pytest.raises(PsectError):
        TextFile.read(path, ENC)


def test_refuses_write_after_outside_change(tmp_path: Path):
    path = tmp_path / "a.c"
    path.write_bytes(b"int a;\n")
    tf = TextFile.read(path, ENC)
    path.write_bytes(b"int changed;\n")
    import os

    os.utime(path, ns=(tf.mtime_ns + 10**9, tf.mtime_ns + 10**9))
    tf.insert(1, ["X;"])
    with pytest.raises(PsectError):
        tf.write()
    assert path.read_bytes() == b"int changed;\n"

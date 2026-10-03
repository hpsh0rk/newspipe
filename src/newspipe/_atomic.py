"""原子写原语（从内部工具库内联而来）。

为什么内联而不是依赖外部工具库：`state.py` 需要「崩溃不产生空文件」的持久写。这是 30 行
标准库代码，把它留成外部依赖会让整个包绑死在别人的仓库上。
实现逐字保留（`fsync` 临时文件后再 `os.replace`），行为不变。

`atomic_append_line` 是给 append-only 操作日志用的：它先拒绝「最后一行已被截断」的文件，
追加后再读回来比对整份内容，避免一次撕裂写污染此后所有读者。
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _temp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int | None = None,
    dir_mode: int | None = None,
) -> None:
    """Replace `path` with `data` durably.

    Existing permission bits are preserved; `mode` forces them instead, which
    private state files rely on.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        try:
            path.parent.chmod(dir_mode)
        except OSError:
            # A parent we do not own is still usable; the file mode below is
            # what actually protects the contents.
            pass
    if mode is None and path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)

    temp = _temp_sibling(path)
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
    dir_mode: int | None = None,
) -> None:
    atomic_write_bytes(Path(path), text.encode(encoding), mode=mode, dir_mode=dir_mode)


def write_json_atomic(path: Path, payload: object, *, sort_keys: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys)
    atomic_write_text(Path(path), text + "\n")


@contextmanager
def directory_lock(directory: Path) -> Iterator[None]:
    """Serialise writers that append to files in `directory`.

    Locks the directory itself rather than the target file so that a writer
    replacing the file cannot strand the lock on an unlinked inode.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as error:
            # Some filesystems refuse advisory locks on directories. Losing
            # mutual exclusion is better than refusing to record the event.
            if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
            yield
            return
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class AppendIntegrityError(Exception):
    pass


def atomic_append_line(
    path: Path,
    line: str,
    *,
    encoding: str = "utf-8",
    error: type[Exception] = AppendIntegrityError,
) -> None:
    """Append exactly one newline-terminated line, then prove the tail is intact.

    Used for the append-only operation logs. Every later run reads these back,
    so a torn or interleaved write would corrupt not just this record but every
    reader afterwards. The sequence is: take the directory lock, refuse a log
    whose final line is already truncated, append, fsync, then re-read and
    confirm the file is exactly what it was plus this line.
    """
    path = Path(path)
    if "\n" in line:
        raise ValueError("append line must not contain a newline")
    blob = (line + "\n").encode(encoding)

    with directory_lock(path.parent):
        with path.open("a+b") as handle:
            handle.seek(0)
            existing = handle.read()
            if existing and not existing.endswith(b"\n"):
                raise error(
                    f"{path.name} has a truncated final line; repair it before appending"
                )

            handle.seek(0, os.SEEK_END)
            written_count = handle.write(blob)
            if written_count != len(blob):
                raise error(f"{path.name} append was shorter than the encoded record")
            handle.flush()
            os.fsync(handle.fileno())

            handle.seek(0)
            written = handle.read()
            if written != existing + blob:
                raise error(f"{path.name} tail does not match the appended record")

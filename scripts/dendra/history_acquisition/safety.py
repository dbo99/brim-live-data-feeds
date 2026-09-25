"""Strict serialization and descriptor-relative, no-follow local I/O."""
import hashlib
import json
import math
import os
import stat
from contextlib import contextmanager
from pathlib import Path


class Hold(ValueError):
    """Preserve evidence and stop; never turn an integrity failure into absence."""


def require(ok, message):
    if not ok:
        raise Hold(message)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def digest(value):
    return sha(encode(value))


def decode(body):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "Duplicate JSON key")
            out[key] = value
        return out

    def number(value):
        result = float(value)
        require(math.isfinite(result), "Nonfinite JSON number")
        return result

    def constant(_):
        raise Hold("Nonstandard JSON constant")

    try:
        return json.loads(body, object_pairs_hook=pairs, parse_float=number,
                          parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Hold("Invalid JSON") from exc


class Root:
    """All traversal stays anchored to open directory fds; no symlink following.

    The caller explicitly supplies an existing task-owned root. Files are never
    replaced. A failed exclusive write may leave a torn file for diagnosis.
    """
    def __init__(self, path):
        path = Path(path)
        require(path.is_absolute() and ".." not in path.parts, "Explicit absolute root required")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def parts(name):
        require(isinstance(name, str) and name and not name.startswith("/"),
                "Relative confined path required")
        parts = name.split("/")
        require(all(p not in ("", ".", "..") and "\0" not in p and "\\" not in p for p in parts),
                "Path traversal or ambiguous path")
        return parts

    @contextmanager
    def directory(self, parts, create=False):
        fd = os.dup(self.fd)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def mkdir(self, name):
        with self.directory(self.parts(name), create=True):
            pass

    def list(self, name=None, limit=4096):
        with self.directory(self.parts(name) if name else []) as fd:
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    require(len(names) < limit, "Directory entry bound")
                    names.append(entry.name)
            return sorted(names)

    def read(self, name, limit):
        parts = self.parts(name)
        with self.directory(parts[:-1]) as parent:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Regular unlinked file required")
                require(info.st_size <= limit, "File exceeds bound")
                with os.fdopen(os.dup(fd), "rb") as f:
                    body = f.read(limit + 1)
                require(len(body) == info.st_size and len(body) <= limit, "File changed or oversized")
                return body
            finally:
                os.close(fd)

    def write_new(self, name, body, limit):
        require(isinstance(body, bytes) and len(body) <= limit, "Write exceeds bound")
        parts = self.parts(name)
        with self.directory(parts[:-1], create=True) as parent:
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(os.dup(fd), "wb") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                os.close(fd)
            os.fsync(parent)

    def lock_fd(self, name):
        parts = self.parts(name)
        with self.directory(parts[:-1]) as parent:
            fd = os.open(parts[-1], os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(fd)
                raise Hold("Invalid writer lock")
            return fd

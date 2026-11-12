"""A minimal, dependency free reader for OLE2 / Compound File Binary files.

Legacy Word (``.doc``), Excel (``.xls``) and WPS (``.wps`` / ``.et``) files are
all OLE2 compound documents.  ``olefile`` is not available in this environment
so the container is parsed here directly.

Only reading is implemented - that is all the parsers need.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

MAXREGSECT = 0xFFFFFFFA
DIFSECT = 0xFFFFFFFC
FATSECT = 0xFFFFFFFD
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF


@dataclass
class DirEntry:
    name: str
    entry_type: int  # 1 storage, 2 stream, 5 root
    start_sector: int
    size: int
    children: list["DirEntry"]


class CFBError(Exception):
    pass


class CompoundFile:
    """Read-only OLE2 compound file reader."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != OLE_SIGNATURE:
            raise CFBError("not an OLE2 compound file")
        self._data = data
        self._parse_header()
        self._read_fat()
        self._read_directory()
        self._read_minifat()

    # -- header ------------------------------------------------------------
    def _parse_header(self) -> None:
        data = self._data
        (sector_shift,) = struct.unpack_from("<H", data, 0x1E)
        (mini_sector_shift,) = struct.unpack_from("<H", data, 0x20)
        (self.num_fat_sectors,) = struct.unpack_from("<I", data, 0x2C)
        (self.first_dir_sector,) = struct.unpack_from("<I", data, 0x30)
        (self.mini_cutoff,) = struct.unpack_from("<I", data, 0x38)
        (self.first_minifat_sector,) = struct.unpack_from("<I", data, 0x3C)
        (self.num_minifat_sectors,) = struct.unpack_from("<I", data, 0x40)
        (self.first_difat_sector,) = struct.unpack_from("<I", data, 0x44)
        (self.num_difat_sectors,) = struct.unpack_from("<I", data, 0x48)

        self.sector_size = 1 << sector_shift
        self.mini_sector_size = 1 << mini_sector_shift
        self.mini_stream = b""
        self.fat: list[int] = []
        self.minifat: list[int] = []
        self.entries: list[DirEntry] = []
        self.root: DirEntry | None = None

    # -- sectors -----------------------------------------------------------
    def _sector_offset(self, sector: int) -> int:
        return (sector + 1) * self.sector_size

    def _read_sector(self, sector: int) -> bytes:
        start = self._sector_offset(sector)
        chunk = self._data[start : start + self.sector_size]
        if len(chunk) < self.sector_size:
            chunk = chunk + b"\x00" * (self.sector_size - len(chunk))
        return chunk

    def _read_fat(self) -> None:
        difat: list[int] = list(
            struct.unpack_from("<109I", self._data, 0x4C)
        )
        sector = self.first_difat_sector
        entries_per_sector = self.sector_size // 4
        seen = 0
        while sector not in (ENDOFCHAIN, FREESECT) and seen < 1 << 20:
            chunk = self._read_sector(sector)
            values = struct.unpack_from(f"<{entries_per_sector}I", chunk, 0)
            difat.extend(values[:-1])
            sector = values[-1]
            seen += 1

        self.fat = []
        for fat_sector in difat:
            if len(self.fat) >= self.num_fat_sectors:
                break
            if fat_sector > MAXREGSECT:
                continue
            chunk = self._read_sector(fat_sector)
            self.fat.extend(struct.unpack_from(f"<{entries_per_sector}I", chunk, 0))

    def _chain(self, start: int) -> list[int]:
        chain: list[int] = []
        sector = start
        guard = 0
        while sector <= MAXREGSECT and guard < 1 << 24:
            chain.append(sector)
            if sector >= len(self.fat):
                break
            sector = self.fat[sector]
            guard += 1
        return chain

    def _read_stream_chain(self, start: int, size: int | None = None) -> bytes:
        parts = [self._read_sector(s) for s in self._chain(start)]
        blob = b"".join(parts)
        if size is not None:
            blob = blob[:size]
        return blob

    def _read_minifat(self) -> None:
        if self.first_minifat_sector > MAXREGSECT:
            self.minifat = []
            return
        entries_per_sector = self.sector_size // 4
        minifat: list[int] = []
        for sector in self._chain(self.first_minifat_sector):
            chunk = self._read_sector(sector)
            minifat.extend(struct.unpack_from(f"<{entries_per_sector}I", chunk, 0))
        self.minifat = minifat

    # -- directory ---------------------------------------------------------
    def _read_directory(self) -> None:
        raw = self._read_stream_chain(self.first_dir_sector)
        entries: list[DirEntry] = []
        for offset in range(0, len(raw) - 127, 128):
            chunk = raw[offset : offset + 128]
            name_len = struct.unpack_from("<H", chunk, 0x40)[0]
            if name_len > 64:
                name_len = 64
            name = chunk[: max(name_len - 2, 0)].decode("utf-16-le", errors="ignore")
            entry_type = chunk[0x42]
            (start_sector,) = struct.unpack_from("<I", chunk, 0x74)
            (size_low,) = struct.unpack_from("<I", chunk, 0x78)
            (size_high,) = struct.unpack_from("<I", chunk, 0x7C)
            size = size_low | (size_high << 32)
            if len(name) == 0 and entry_type == 0:
                continue
            entries.append(DirEntry(name, entry_type, start_sector, size, []))

        self.entries = entries
        if entries:
            self.root = entries[0]
            # The root entry's stream is the mini stream container.
            if self.root.size:
                self.mini_stream = self._read_stream_chain(
                    self.root.start_sector, self.root.size
                )

    # -- public API --------------------------------------------------------
    def stream_names(self) -> list[str]:
        return [entry.name for entry in self.entries if entry.entry_type == 2]

    def has_stream(self, name: str) -> bool:
        return any(
            entry.entry_type == 2 and entry.name == name for entry in self.entries
        )

    def read_stream(self, name: str) -> bytes | None:
        for entry in self.entries:
            if entry.entry_type == 2 and entry.name == name:
                if entry.size < self.mini_cutoff and self.mini_stream:
                    return self._read_mini_stream(entry)
                return self._read_stream_chain(entry.start_sector, entry.size)
        return None

    def _read_mini_stream(self, entry: DirEntry) -> bytes:
        parts: list[bytes] = []
        sector = entry.start_sector
        guard = 0
        while sector <= MAXREGSECT and guard < 1 << 22:
            offset = sector * self.mini_sector_size
            parts.append(
                self.mini_stream[offset : offset + self.mini_sector_size]
            )
            if sector >= len(self.minifat):
                break
            sector = self.minifat[sector]
            guard += 1
        return b"".join(parts)[: entry.size]

    def iter_streams(self):
        for entry in self.entries:
            if entry.entry_type != 2:
                continue
            data = self.read_stream(entry.name)
            if data is not None:
                yield entry.name, data

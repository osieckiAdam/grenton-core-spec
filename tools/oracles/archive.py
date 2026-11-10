"""The interface archive and the Object Manager response, written from spec
§4.1–§4.3 (conformance.md §4d). Never derived from a port.

Python's zipfile trusts the sizes the headers declare, so entries are
inflated here with zlib and counted, as §4.3 items 4–5 require; zipfile only
lists the central directory.
"""
import io
import json
import struct
import unicodedata
import zipfile
import zlib

LIMIT = 10 * 1024 * 1024          # §4.3 item 4: 10 MiB
ENTRY = "mygrenton/data.json"     # §4.3 item 2
ROOT_FIELDS = ("id", "version", "name", "icon", "theme", "encryption", "clus", "pages")


class ArchiveError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def invalid():
    return ArchiveError("provisioning.invalidData")


def inflate(data, info):
    """The entry's bytes, counted while inflating; raises on a lie."""
    offset = info.header_offset
    if data[offset:offset + 4] != b"PK\x03\x04":
        raise invalid()
    name_len, extra_len = struct.unpack("<HH", data[offset + 26:offset + 30])
    start = offset + 30 + name_len + extra_len
    raw = data[start:start + info.compress_size]
    if info.compress_type == zipfile.ZIP_STORED:
        out = raw[:LIMIT + 1]
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        d = zlib.decompressobj(-15)
        try:
            out = d.decompress(raw, LIMIT + 1)
        except zlib.error:
            raise invalid()
    else:
        raise invalid()
    if len(out) > LIMIT:
        raise invalid()
    if len(out) != info.file_size or zlib.crc32(out) != info.CRC:
        raise invalid()
    return out


def read_interface(data):
    """§4.3: archive bytes → the decoded data.json root."""
    if len(data) > LIMIT:
        raise invalid()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, struct.error):
        raise invalid()
    matches = [i for i in infos if i.filename == ENTRY]   # exact, byte for byte
    if len(matches) != 1 or matches[0].is_dir():
        raise invalid()
    raw = inflate(data, matches[0])
    try:
        root = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise invalid()
    if not isinstance(root, dict) or not all(k in root for k in ROOT_FIELDS):
        raise invalid()
    return root


def interface_projection(root):
    """`interface` as in conformance.md §4c."""
    from oracles.omp import CONFORMANCE_SKIP
    from oracles.mapping import PRESENTATIONAL, SUPPORTED
    header = {k: root[k] for k in ("id", "version", "name", "icon", "theme", "encryption", "clus",
                                   "pushNotifications")}
    header["pages"] = [{"name": p["name"], "icon": p["icon"], "isFullscreenWidget": p["isFullscreenWidget"],
                        "widgets": [{"id": w["id"], "type": w["type"]} for w in p["widgets"]
                                    if CONFORMANCE_SKIP not in w and w.get("type") in SUPPORTED | PRESENTATIONAL]}
                       for p in root["pages"]]
    return header


def archive_value(data):
    from oracles.mapping import map_interface
    try:
        root = read_interface(data)
    except ArchiveError as e:
        return {"outcome": "error", "code": e.code}
    return {"outcome": "ok", "value": {"interface": interface_projection(root), "mapping": map_interface(root)}}


# §4.1: the PIN without White_Space, one path segment.
KEEP = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@")


def is_white_space(ch):
    # Unicode White_Space: Zs, plus the listed controls and separators.
    return ch in "\t\n\x0b\x0c\r\x85  " or unicodedata.category(ch) == "Zs"


def pin_segment(pin):
    compact = "".join(ch for ch in pin if not is_white_space(ch))
    return compact, "".join(ch if ch in KEEP else "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
                            for ch in compact)


def fetch_value(inp, body_bytes):
    """The expected outcome of a provisioning.fetch case."""
    if inp.get("unreachable"):
        return {"outcome": "error", "code": "provisioning.connection"}
    status = inp["response"]["status"]
    if status == 401:
        return {"outcome": "error", "code": "provisioning.authenticationFailed"}
    if status != 200:      # §4.2: every other status, 3xx included
        return {"outcome": "error", "code": "provisioning.connection"}
    got = archive_value(body_bytes)
    if got["outcome"] == "ok":
        _, encoded = pin_segment(inp["pin"])
        got["value"] = {"requestTarget": "/api/v1/interface/hash/" + encoded, **got["value"]}
    return got

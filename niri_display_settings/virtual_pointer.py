"""Minimal raw-Wayland client for zwlr_virtual_pointer_manager_v1.

niri does not reposition the cursor when outputs are rearranged; on the next
physical mouse motion it teleports a stranded cursor to the center of the
first output.  To avoid that jarring jump we warp the cursor ourselves to a
sensible location right after applying output changes.

niri has no cursor IPC, but it does implement the wlr virtual pointer
protocol, so we speak the Wayland wire format directly over the socket —
about a hundred lines, no dependencies.
"""

from __future__ import annotations

import os
import socket
import struct

MANAGER_IFACE = b"zwlr_virtual_pointer_manager_v1"


def _msg(obj_id: int, opcode: int, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return struct.pack("<II", obj_id, (size << 16) | opcode) + payload


def _wl_string(s: bytes) -> bytes:
    data = s + b"\0"
    pad = (-len(data)) % 4
    return struct.pack("<I", len(data)) + data + b"\0" * pad


def _connect() -> socket.socket:
    display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    if not os.path.isabs(display):
        runtime = os.environ["XDG_RUNTIME_DIR"]
        display = os.path.join(runtime, display)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    sock.connect(display)
    return sock


def _read_events(sock: socket.socket, until_obj: int, until_opcode: int) -> list[tuple]:
    """Collect (obj, opcode, payload) until a given event arrives."""
    events = []
    buf = b""
    while True:
        while len(buf) < 8:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("wayland socket closed")
            buf += chunk
        obj, sizeop = struct.unpack_from("<II", buf)
        size, opcode = sizeop >> 16, sizeop & 0xFFFF
        while len(buf) < size:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("wayland socket closed")
            buf += chunk
        payload = buf[8:size]
        buf = buf[size:]
        events.append((obj, opcode, payload))
        if obj == until_obj and opcode == until_opcode:
            return events


def warp(x: int, y: int, x_extent: int, y_extent: int) -> bool:
    """Warp the cursor to (x, y) within the global output bounding box.

    Coordinates are logical pixels relative to the bounding box origin;
    extents are the bounding box dimensions.  Returns False on any failure
    (missing protocol, sandboxed client, connection error, ...).
    """
    try:
        sock = _connect()
    except OSError:
        return False
    try:
        REGISTRY, CALLBACK, MANAGER, POINTER = 2, 3, 4, 5
        # wl_display.get_registry(2) + wl_display.sync(3)
        sock.sendall(_msg(1, 1, struct.pack("<I", REGISTRY)) +
                     _msg(1, 0, struct.pack("<I", CALLBACK)))
        events = _read_events(sock, CALLBACK, 0)  # wl_callback.done

        manager_name = None
        for obj, opcode, payload in events:
            if obj == REGISTRY and opcode == 0:  # wl_registry.global
                name, = struct.unpack_from("<I", payload)
                slen, = struct.unpack_from("<I", payload, 4)
                iface = payload[8:8 + slen - 1]
                if iface == MANAGER_IFACE:
                    manager_name = name
        if manager_name is None:
            return False

        # wl_registry.bind(name, interface, version=1, new_id=MANAGER)
        bind = (struct.pack("<I", manager_name) + _wl_string(MANAGER_IFACE)
                + struct.pack("<II", 1, MANAGER))
        # manager.create_virtual_pointer(seat=null, id=POINTER)
        create = _msg(MANAGER, 0, struct.pack("<II", 0, POINTER))
        # pointer.motion_absolute(time, x, y, x_extent, y_extent) + frame
        motion = _msg(POINTER, 1, struct.pack("<IIIII", 0, x, y, x_extent, y_extent))
        frame = _msg(POINTER, 4)
        destroy = _msg(POINTER, 8)
        # wl_display.sync to make sure everything got processed
        sync2 = _msg(1, 0, struct.pack("<I", 6))
        sock.sendall(_msg(REGISTRY, 0, bind) + create + motion + frame
                     + destroy + sync2)
        for obj, opcode, payload in _read_events(sock, 6, 0):
            if obj == 1 and opcode == 0:  # wl_display.error
                return False
        return True
    except (OSError, ConnectionError, struct.error):
        return False
    finally:
        sock.close()

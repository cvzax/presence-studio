"""Local Discord Rich Presence connection for Windows."""

import ctypes
from ctypes import wintypes
import json
import msvcrt
import os
import struct
import time
import uuid

HEADER = struct.Struct("<II")
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
peek = kernel.PeekNamedPipe
peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                 wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
peek.restype = wintypes.BOOL


class DiscordError(Exception):
    pass


class DiscordRPC:
    def __init__(self, app_id):
        self.pipe = None
        for index in range(10):
            try:
                self.pipe = open(rf"\\.\pipe\discord-ipc-{index}", "r+b", buffering=0)
                break
            except OSError:
                continue
        if self.pipe is None:
            raise DiscordError("Открой настольный Discord и войди в аккаунт.")
        try:
            self.send(0, {"v": 1, "client_id": app_id})
            reply = self.receive()
            if reply.get("evt") != "READY":
                raise DiscordError("Discord отклонил подключение. Проверь Application ID.")
        except Exception:
            self.close()
            raise

    def close(self):
        self.pipe.close()

    def send(self, opcode, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        packet = HEADER.pack(opcode, len(body)) + body
        while packet:
            written = self.pipe.write(packet)
            if not written:
                raise DiscordError("Соединение с Discord закрыто.")
            packet = packet[written:]

    def read_exactly(self, count, deadline):
        handle = msvcrt.get_osfhandle(self.pipe.fileno())
        available = wintypes.DWORD()
        result = bytearray()
        while len(result) < count:
            if time.monotonic() >= deadline:
                raise DiscordError("Discord не ответил вовремя.")
            if not peek(handle, None, 0, None, ctypes.byref(available), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not available.value:
                time.sleep(0.02)
                continue
            chunk = self.pipe.read(min(count - len(result), available.value))
            if not chunk:
                raise DiscordError("Соединение с Discord закрыто.")
            result.extend(chunk)
        return result

    def read_frame(self, deadline):
        opcode, length = HEADER.unpack(self.read_exactly(HEADER.size, deadline))
        if length > 1024 * 1024:
            raise DiscordError("Discord прислал слишком большой ответ.")
        payload = json.loads(self.read_exactly(length, deadline))
        if opcode == 2:
            raise DiscordError("Discord закрыл соединение. Проверь Application ID.")
        if opcode == 3:
            self.send(4, payload)
        return opcode, payload

    def poll(self):
        handle = msvcrt.get_osfhandle(self.pipe.fileno())
        available = wintypes.DWORD()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            raise DiscordError("Соединение с Discord прервалось.")
        if available.value:
            self.read_frame(time.monotonic() + 4)

    def receive(self, nonce=None):
        deadline = time.monotonic() + 4
        while True:
            opcode, payload = self.read_frame(deadline)
            if opcode == 1 and (nonce is None or payload.get("nonce") == nonce):
                if payload.get("evt") == "ERROR":
                    message = payload.get("data", {}).get("message", "Неизвестная ошибка")
                    raise DiscordError(f"Discord отклонил запрос: {message}")
                return payload

    def set_activity(self, activity):
        nonce = uuid.uuid4().hex
        self.send(1, {"cmd": "SET_ACTIVITY", "nonce": nonce,
                      "args": {"pid": os.getpid(), "activity": activity}})
        return self.receive(nonce)

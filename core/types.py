from dataclasses import dataclass
import json
import time
import uuid


@dataclass(frozen=True)
class Scope:
    platform: str
    owner: str
    room: str

    @property
    def key(self):
        return json.dumps([self.platform, self.owner, self.room], separators=(',', ':'))


def now():
    return int(time.time())


def ident():
    return uuid.uuid4().hex


class Failure(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def error_data(exc):
    message = str(exc) if isinstance(exc, Failure) else f'处理异常（{type(exc).__name__}）：{exc}'
    if isinstance(exc, TimeoutError):
        message = '模型请求超时；已保留断点，调用用量待核对'
    return {'code': exc.code if isinstance(exc, Failure) else type(exc).__name__,
            'message': message, 'retryable': isinstance(exc, Failure) and exc.retryable}

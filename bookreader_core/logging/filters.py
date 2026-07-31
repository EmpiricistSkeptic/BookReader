import logging, contextvars

_request_id = contextvars.ContextVar("request_id", default="-")
_user_id = contextvars.ContextVar("user_id", default="-")

def set_request_context(rid, uid):
    _request_id.set(rid or "-")
    _user_id.set(uid or "-")

class RequestContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id.get()
        record.user_id = _user_id.get()
        return True
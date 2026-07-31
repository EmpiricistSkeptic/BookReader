from bookreader_core.logging.filters import set_request_context
import uuid

class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        uid = getattr(getattr(request, "user", None), "id", None)
        set_request_context(rid, uid)
        response = self.get_response(request)
        response["X-Request-ID"] = rid
        return response
    

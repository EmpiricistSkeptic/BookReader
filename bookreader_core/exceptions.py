from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.response import Response
from rest_framework import status
import logging

from reader.exceptions import TranslationServiceError, AIServiceError

logger = logging.getLogger("reader")

def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    req, view = context.get("request"), context.get("view")
    
    if response is not None:
        logger.error("DRF error: view=%s path=%s method=%s exc=%r",
                     getattr(view, "__class__", type(view)).__name__,
                     getattr(req, "path", "-"),
                     getattr(req, "method", "-"), exc, exc_info=True)
        return response
    
    if isinstance(exc, TranslationServiceError):
        return Response({
            "detail": str(exc), "service": "translation"
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    elif isinstance(exc, AIServiceError):
        return Response({
            "detail": str(exc), "service": "ai_service"
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    logger.exception("Unhandled error: view=%s path=%s method=%s exc=%r",
                     getattr(view, "__class__", type(view)).__name__,
                     getattr(req, "path", "-"),
                     getattr(req, "method", "-"), 
                     exc,
                     )
    return Response({
        "detail": str(exc)
    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

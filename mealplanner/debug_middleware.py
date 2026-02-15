import logging

logger = logging.getLogger(__name__)

class DebugHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        logger.debug(f"Request headers: {dict(request.headers)}")
        logger.debug(f"Request META HTTP_X_FORWARDED_PROTO: {request.META.get('HTTP_X_FORWARDED_PROTO')}")
        logger.debug(f"Request is_secure: {request.is_secure()}")
        logger.debug(f"Request scheme: {request.scheme}")
        response = self.get_response(request)
        return response

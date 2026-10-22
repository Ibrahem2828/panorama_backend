from rest_framework.negotiation import BaseContentNegotiation


class IgnoreClientContentNegotiation(BaseContentNegotiation):
    """Skip ``Accept`` matching for views that stream a file.

    The body is a ready-made PNG, not a DRF-rendered payload, so a client asking for ``image/*`` (as mobile image
    loaders do) must not be answered 406. Errors still render with the default (JSON) renderer.
    """

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)

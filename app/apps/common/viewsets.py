from typing import cast

from rest_framework import status, viewsets

from .responses import success_response


class StandardReadResponseMixin:
    """Standard envelopes for the two methods safe for read-only resources."""

    def list(self, request, *args, **kwargs):
        view = cast(viewsets.GenericViewSet, self)
        queryset = view.filter_queryset(view.get_queryset())
        page = view.paginate_queryset(queryset)
        if page is not None:
            serializer = view.get_serializer(page, many=True)
            return view.get_paginated_response(serializer.data)
        serializer = view.get_serializer(queryset, many=True)
        return success_response(data=serializer.data, request=request)

    def retrieve(self, request, *args, **kwargs):
        view = cast(viewsets.GenericViewSet, self)
        serializer = view.get_serializer(view.get_object())
        return success_response(data=serializer.data, request=request)


class StandardWriteResponseMixin:
    """Standard envelopes for explicitly writable model resources."""

    create_success_message = "Created successfully"
    update_success_message = "Updated successfully"

    def create(self, request, *args, **kwargs):
        view = cast(viewsets.ModelViewSet, self)
        serializer = view.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        view.perform_create(serializer)
        return success_response(
            data=serializer.data,
            message=self.create_success_message,
            status_code=status.HTTP_201_CREATED,
            request=request,
        )

    def update(self, request, *args, **kwargs):
        view = cast(viewsets.ModelViewSet, self)
        partial = kwargs.pop("partial", False)
        serializer = view.get_serializer(view.get_object(), data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        view.perform_update(serializer)
        return success_response(data=serializer.data, message=self.update_success_message, request=request)


class StandardDestroyMixin:
    """Soft-delete behavior for model resources that explicitly expose DELETE."""

    delete_success_message = "Deleted successfully"

    def destroy(self, request, *args, **kwargs):
        view = cast(viewsets.GenericViewSet, self)
        instance = view.get_object()
        if hasattr(instance, "is_deleted"):
            instance.is_deleted = True
        if hasattr(instance, "is_active"):
            instance.is_active = False
        instance.save()
        return success_response(message=self.delete_success_message, request=request)


class StandardModelViewSet(
    StandardReadResponseMixin,
    StandardWriteResponseMixin,
    StandardDestroyMixin,
    viewsets.ModelViewSet,
):
    pass


class StandardReadOnlyModelViewSet(StandardReadResponseMixin, viewsets.ReadOnlyModelViewSet):
    """List/retrieve only.

    DRF routers discover actions by attribute. Write methods must never be added
    to this class, otherwise a nominally read-only resource becomes writable.
    """

    pass


class StandardExplicitActionViewSet(StandardReadResponseMixin, viewsets.GenericViewSet):
    """Base for a deliberately small set of custom, documented write actions."""

    pass

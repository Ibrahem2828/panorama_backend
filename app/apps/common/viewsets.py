from rest_framework import status, viewsets

from .responses import success_response


class StandardResponseMixin:
    create_success_message = "Created successfully"
    update_success_message = "Updated successfully"
    delete_success_message = "Deleted successfully"

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return success_response(data=serializer.data, request=request)

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return success_response(data=serializer.data, request=request)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return success_response(
            data=serializer.data,
            message=self.create_success_message,
            status_code=status.HTTP_201_CREATED,
            request=request,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return success_response(data=serializer.data, message=self.update_success_message, request=request)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if hasattr(instance, "is_deleted"):
            instance.is_deleted = True
        if hasattr(instance, "is_active"):
            instance.is_active = False
        instance.save()
        return success_response(message=self.delete_success_message, request=request)


class StandardModelViewSet(StandardResponseMixin, viewsets.ModelViewSet):
    pass


class StandardReadOnlyModelViewSet(StandardResponseMixin, viewsets.ReadOnlyModelViewSet):
    pass

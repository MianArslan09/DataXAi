class BaseRepository:
    """
    Base class wrapping ORM queries behind a stable interface (the
    Repository pattern from Volume 1, Section 1.4 / your Section 24.5).
    Subclasses set `model` and add query methods; services depend on
    repositories, never directly on `Model.objects`.
    """

    model = None

    def get_by_id(self, pk):
        return self.model.objects.get(pk=pk)

    def all(self):
        return self.model.objects.all()

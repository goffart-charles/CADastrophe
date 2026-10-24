"""A closeable iterator owning a document reservation, including before start."""


class ReservedComputation:
    def __init__(self, document, steps, *, owner=None):
        self.document, self.steps = document, iter(steps)
        self.closed = False
        self.owner = owner
        document.history.reserve(self if owner is None else owner)

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            return next(self.steps)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                close = getattr(self.steps, "close", None)
                if close is not None:
                    close()
            finally:
                self.document.history.release(self if self.owner is None else self.owner)


def complete(steps):
    """Keep synchronous APIs on the exact same incremental evaluation path."""
    try:
        while True:
            next(steps)
    except StopIteration as result:
        return result.value
    finally:
        close = getattr(steps, "close", None)
        if close is not None:
            close()


def cooperative_required(objects, *, threshold=32):
    """Use steps for long histories and costly native surface constructions."""
    from .feature_objects import ConstructionFeature, SweepFeature, LoftFeature, ReplayFeature
    return (sum(isinstance(obj, ConstructionFeature) for obj in objects.values()) >= threshold
            or any(isinstance(obj, (SweepFeature, LoftFeature))
                   or isinstance(obj, ReplayFeature) and obj.parameters.count >= threshold
                   for obj in objects.values()))
